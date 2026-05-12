"""
ternary_net.py — Ternary-weight classifier for NOVA epistemic state.

PoC for the "Ternary Epistemic Memory" idea: a small feed-forward network
whose weights are constrained to {-1, 0, +1} during the forward pass, with
straight-through gradients for backprop. The third value (0) is treated as
a first-class "unknown / abstain" state rather than a compression artifact.

Architecture (defaults):
    TernaryLinear(in=384, out=H=64) -> BatchNorm1d -> ReLU
    -> TernaryLinear(out=3)         -> logits over {false, unknown, true}

This module is intentionally read-only against NOVA — it consumes shards
through store/graph and writes nothing back. The phase-2 feedback loop
into shard confidence/epistemic is explicitly out of scope.

Class index convention used throughout this module and its callers:
    0 = false / contradicted
    1 = unknown / neutral
    2 = true  / confirmed
which matches the `epistemic` digit in nova_shard_db.encode_state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


CLASS_NAMES: tuple[str, str, str] = ("false", "unknown", "true")
INPUT_DIM_DEFAULT = 384  # all-MiniLM-L6-v2 dimension


# ═══════════════════════════════════════════════════════════
# TERNARY QUANTIZER (straight-through estimator)
# ═══════════════════════════════════════════════════════════

if TORCH_AVAILABLE:

    class TernaryQuantize(torch.autograd.Function):
        """Snap weights to {-1, 0, +1} on forward; pass gradient through on backward.

        Threshold delta follows Li & Liu 2016 (TWN): delta = 0.7 * mean(|w|).
        Gradient is clipped to zero outside the [-1, 1] band of the latent
        continuous weight, which stabilizes training when latent weights
        drift far from the quantization band.
        """

        @staticmethod
        def forward(ctx, w: "torch.Tensor") -> "torch.Tensor":  # noqa: F821
            delta = 0.7 * w.abs().mean()
            ctx.save_for_backward(w)
            out = torch.zeros_like(w)
            out[w > delta] = 1.0
            out[w < -delta] = -1.0
            return out

        @staticmethod
        def backward(ctx, grad_out: "torch.Tensor"):  # noqa: F821
            (w,) = ctx.saved_tensors
            grad = grad_out.clone()
            grad[w.abs() > 1.0] = 0.0
            return grad


    class TernaryLinear(nn.Module):
        """Fully-connected layer with ternary forward weights and a real-valued bias.

        Latent weight is continuous (`self.weight`); the forward pass projects
        through TernaryQuantize so the effective weight matrix is ternary. The
        latent weight is what Adam updates.
        """

        def __init__(self, in_features: int, out_features: int):
            super().__init__()
            self.in_features = in_features
            self.out_features = out_features
            self.weight = nn.Parameter(torch.randn(out_features, in_features) * 0.05)
            self.bias = nn.Parameter(torch.zeros(out_features))

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":  # noqa: F821
            w_t = TernaryQuantize.apply(self.weight)
            return F.linear(x, w_t, self.bias)

        def ternary_weight(self) -> "torch.Tensor":  # noqa: F821
            with torch.no_grad():
                return TernaryQuantize.apply(self.weight)

        def zero_weight_rate(self) -> float:
            """Fraction of effective weights that are exactly zero after quantization.

            This is the headline metric for "is the unknown/abstain state being
            used meaningfully" — too low means the network is treating ternary
            as binary; too high means the layer is collapsing.
            """
            wt = self.ternary_weight()
            return (wt == 0).float().mean().item()


    class TernaryNet(nn.Module):
        """Two-layer ternary feedforward classifier over a sentence embedding.

        BatchNorm sits between the two ternary layers to keep activations
        well-scaled despite the discrete weight matrix.
        """

        def __init__(
            self,
            input_dim: int = INPUT_DIM_DEFAULT,
            hidden_dim: int = 64,
            num_classes: int = 3,
        ):
            super().__init__()
            self.input_dim = input_dim
            self.hidden_dim = hidden_dim
            self.num_classes = num_classes

            self.fc1 = TernaryLinear(input_dim, hidden_dim)
            self.bn = nn.BatchNorm1d(hidden_dim)
            self.fc2 = TernaryLinear(hidden_dim, num_classes)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":  # noqa: F821
            h = self.fc1(x)
            h = self.bn(h)
            h = F.relu(h)
            return self.fc2(h)

        def zero_weight_rates(self) -> dict[str, float]:
            return {
                "fc1": self.fc1.zero_weight_rate(),
                "fc2": self.fc2.zero_weight_rate(),
            }


# ═══════════════════════════════════════════════════════════
# DATA / TRAINING UTILITIES (numpy + torch)
# ═══════════════════════════════════════════════════════════

@dataclass
class TrainConfig:
    hidden_dim: int = 64
    epochs: int = 30
    batch_size: int = 32
    lr: float = 1e-3
    seed: int = 1729
    early_stop_patience: int = 5
    val_fraction: float = 0.15
    test_fraction: float = 0.15
    log_every_epoch: bool = True


@dataclass
class TrainHistory:
    train_loss: list[float] = field(default_factory=list)
    val_macro_f1: list[float] = field(default_factory=list)
    zero_weight_rates: list[dict[str, float]] = field(default_factory=list)
    best_epoch: int = -1
    best_val_macro_f1: float = -1.0


def compute_class_weights(y: np.ndarray, num_classes: int = 3) -> np.ndarray:
    """Inverse-frequency class weights normalized to mean=1.

    Empty classes get weight 0 (they contribute nothing to CE) — without this
    the inverse-freq formula would divide by zero. Empty-class predictions
    will then always be wrong, which is the correct behavior: the model has
    no evidence to learn that class.
    """
    counts = np.bincount(y.astype(np.int64), minlength=num_classes).astype(np.float64)
    weights = np.zeros(num_classes, dtype=np.float64)
    nonzero = counts > 0
    weights[nonzero] = 1.0 / counts[nonzero]
    if weights.sum() > 0:
        weights = weights * (nonzero.sum() / weights.sum())
    return weights.astype(np.float32)


def stratified_split(
    y: np.ndarray,
    val_fraction: float,
    test_fraction: float,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-class stratified split into train/val/test index arrays.

    Small classes (n=1 or 2) are placed in train so every split is non-empty
    and so the model gets to see at least one example of the class.
    """
    rng = np.random.default_rng(seed)
    train_idx, val_idx, test_idx = [], [], []
    for cls in np.unique(y):
        idxs = np.where(y == cls)[0]
        rng.shuffle(idxs)
        n = len(idxs)
        if n <= 2:
            train_idx.extend(idxs.tolist())
            continue
        n_test = max(1, int(round(n * test_fraction)))
        n_val = max(1, int(round(n * val_fraction)))
        n_train = n - n_val - n_test
        if n_train < 1:
            n_train, n_val, n_test = n - 2, 1, 1
        train_idx.extend(idxs[:n_train].tolist())
        val_idx.extend(idxs[n_train : n_train + n_val].tolist())
        test_idx.extend(idxs[n_train + n_val :].tolist())
    return (
        np.array(sorted(train_idx), dtype=np.int64),
        np.array(sorted(val_idx), dtype=np.int64),
        np.array(sorted(test_idx), dtype=np.int64),
    )


def per_class_metrics(y_true: np.ndarray, y_pred: np.ndarray, num_classes: int = 3) -> dict:
    """Per-class precision/recall/F1 plus macro-F1 and confusion matrix."""
    confusion = np.zeros((num_classes, num_classes), dtype=np.int64)
    for t, p in zip(y_true.astype(np.int64), y_pred.astype(np.int64)):
        if 0 <= t < num_classes and 0 <= p < num_classes:
            confusion[t, p] += 1

    per_class = {}
    f1s = []
    for c in range(num_classes):
        tp = int(confusion[c, c])
        fp = int(confusion[:, c].sum() - tp)
        fn = int(confusion[c, :].sum() - tp)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[CLASS_NAMES[c]] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": int(confusion[c, :].sum()),
        }
        f1s.append(f1)

    return {
        "macro_f1": round(float(np.mean(f1s)), 4),
        "per_class": per_class,
        "confusion": confusion.tolist(),
    }


def _iter_batches(X: np.ndarray, y: np.ndarray, batch_size: int, shuffle: bool, rng: np.random.Generator):
    n = len(X)
    order = np.arange(n)
    if shuffle:
        rng.shuffle(order)
    for start in range(0, n, batch_size):
        idxs = order[start : start + batch_size]
        yield X[idxs], y[idxs]


def train_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    config: TrainConfig,
) -> tuple["TernaryNet", TrainHistory]:  # noqa: F821
    """Train a TernaryNet with class-weighted CE and early stopping on val macro-F1."""
    if not TORCH_AVAILABLE:
        raise RuntimeError("torch is not installed — install requirements.txt")

    torch.manual_seed(config.seed)
    rng = np.random.default_rng(config.seed)

    input_dim = X_train.shape[1]
    model = TernaryNet(input_dim=input_dim, hidden_dim=config.hidden_dim, num_classes=3)
    optim = torch.optim.Adam(model.parameters(), lr=config.lr)

    class_weights = compute_class_weights(y_train, num_classes=3)
    loss_fn = nn.CrossEntropyLoss(weight=torch.from_numpy(class_weights))

    history = TrainHistory()
    best_state = None
    patience = 0

    for epoch in range(config.epochs):
        model.train()
        total_loss = 0.0
        n_batches = 0
        for xb, yb in _iter_batches(X_train, y_train, config.batch_size, shuffle=True, rng=rng):
            if len(xb) < 2:
                continue  # BatchNorm needs at least 2
            xb_t = torch.from_numpy(xb.astype(np.float32))
            yb_t = torch.from_numpy(yb.astype(np.int64))
            optim.zero_grad()
            logits = model(xb_t)
            loss = loss_fn(logits, yb_t)
            loss.backward()
            optim.step()
            total_loss += float(loss.item())
            n_batches += 1
        avg_loss = total_loss / max(n_batches, 1)

        val_pred = predict(model, X_val)
        val_metrics = per_class_metrics(y_val, val_pred)
        val_f1 = val_metrics["macro_f1"]

        history.train_loss.append(round(avg_loss, 4))
        history.val_macro_f1.append(val_f1)
        history.zero_weight_rates.append(model.zero_weight_rates())

        if val_f1 > history.best_val_macro_f1:
            history.best_val_macro_f1 = val_f1
            history.best_epoch = epoch
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= config.early_stop_patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


def predict(model: "TernaryNet", X: np.ndarray) -> np.ndarray:  # noqa: F821
    """Argmax prediction on numpy input. Handles BatchNorm's n=1 edge case."""
    model.eval()
    with torch.no_grad():
        x_t = torch.from_numpy(X.astype(np.float32))
        if len(x_t) == 1:
            # BatchNorm1d with a single sample in eval mode is fine; just guard.
            logits = model(x_t)
        else:
            logits = model(x_t)
        return logits.argmax(dim=1).cpu().numpy().astype(np.int64)


def predict_proba(model: "TernaryNet", X: np.ndarray) -> np.ndarray:  # noqa: F821
    model.eval()
    with torch.no_grad():
        x_t = torch.from_numpy(X.astype(np.float32))
        return F.softmax(model(x_t), dim=1).cpu().numpy()


def evaluate(model: "TernaryNet", X: np.ndarray, y: np.ndarray) -> dict:  # noqa: F821
    """Full evaluation report including per-class metrics + zero-weight rates."""
    y_pred = predict(model, X)
    report = per_class_metrics(y, y_pred)
    report["zero_weight_rate"] = model.zero_weight_rates()
    return report


def save_checkpoint(model: "TernaryNet", meta: dict, path: str | Path) -> None:  # noqa: F821
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "meta": meta}, str(path))


def load_checkpoint(path: str | Path) -> tuple["TernaryNet", dict]:  # noqa: F821
    obj: Any = torch.load(str(path), map_location="cpu", weights_only=False)
    meta = obj["meta"]
    model = TernaryNet(
        input_dim=int(meta.get("input_dim", INPUT_DIM_DEFAULT)),
        hidden_dim=int(meta.get("hidden_dim", 64)),
        num_classes=int(meta.get("num_classes", 3)),
    )
    model.load_state_dict(obj["state_dict"])
    return model, meta


# ═══════════════════════════════════════════════════════════
# LABEL DERIVATION HELPERS (shared with export utility)
# ═══════════════════════════════════════════════════════════

def epistemic_from_confidence(
    confidence: float,
    low_threshold: float = 0.40,
    high_threshold: float = 0.85,
) -> int:
    """Bin a confidence float to {0, 1, 2}.

    Defaults match `mcp/nova_shard_db._epistemic_from_confidence` exactly so
    the model trains against NOVA's own ontology. Alternate edges are usable
    by the threshold-sensitivity sweep but should never become the primary.
    """
    if confidence >= high_threshold:
        return 2
    if confidence < low_threshold:
        return 0
    return 1
