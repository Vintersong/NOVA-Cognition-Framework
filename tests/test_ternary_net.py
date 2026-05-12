"""Smoke tests for the Ternary Epistemic Memory PoC.

These verify:
  - The ternary quantizer produces only {-1, 0, +1} and propagates a gradient.
  - A short training pass on synthetic data converges without NaN.
  - The dataset export pipeline reads shards, derives labels from epistemic
    state, and applies graph-edge refinement correctly.

Tests skip cleanly when torch isn't installed so the rest of the suite
remains green on environments that haven't yet `pip install -r mcp/requirements.txt`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UTIL_DIR = REPO_ROOT / "utilities"
if str(UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(UTIL_DIR))

torch = pytest.importorskip("torch", reason="torch not installed; see mcp/requirements.txt")

from ternary_net import (  # noqa: E402  (after importorskip)
    CLASS_NAMES,
    TernaryLinear,
    TernaryNet,
    TernaryQuantize,
    TrainConfig,
    compute_class_weights,
    epistemic_from_confidence,
    evaluate,
    per_class_metrics,
    predict,
    stratified_split,
    train_model,
)
from export_ternary_dataset import (  # noqa: E402
    base_label,
    build_dataset,
    build_input_text,
    refine_with_graph,
    strip_vocab,
    EPISTEMIC_VOCAB,
)


# ═══════════════════════════════════════════════════════════
# QUANTIZER
# ═══════════════════════════════════════════════════════════

def test_ternary_quantize_produces_only_tri_values():
    torch.manual_seed(0)
    w = torch.randn(8, 16)
    out = TernaryQuantize.apply(w)
    unique = set(torch.unique(out).tolist())
    assert unique.issubset({-1.0, 0.0, 1.0})


def test_ternary_quantize_has_some_zeros():
    """At small init scales, threshold ~0.7*mean(|w|) should leave a chunk at 0."""
    torch.manual_seed(0)
    w = torch.randn(32, 64) * 0.05
    out = TernaryQuantize.apply(w)
    zero_rate = (out == 0).float().mean().item()
    assert 0.05 < zero_rate < 0.95


def test_ternary_quantize_gradient_flows():
    w = torch.randn(4, 4, requires_grad=True)
    out = TernaryQuantize.apply(w)
    loss = (out ** 2).sum()
    loss.backward()
    assert w.grad is not None
    assert w.grad.abs().sum().item() > 0


def test_ternary_linear_forward_shape_and_zero_rate():
    layer = TernaryLinear(16, 8)
    x = torch.randn(4, 16)
    y = layer(x)
    assert y.shape == (4, 8)
    zr = layer.zero_weight_rate()
    assert 0.0 <= zr <= 1.0


def test_ternary_net_forward_shape():
    net = TernaryNet(input_dim=384, hidden_dim=32, num_classes=3)
    x = torch.randn(5, 384)
    logits = net(x)
    assert logits.shape == (5, 3)
    rates = net.zero_weight_rates()
    assert set(rates.keys()) == {"fc1", "fc2"}


# ═══════════════════════════════════════════════════════════
# TRAINING / METRICS
# ═══════════════════════════════════════════════════════════

def _make_synthetic_dataset(n_per_class: int = 60, dim: int = 32, seed: int = 0):
    """Three Gaussian clusters with disjoint centers — easy 3-class task."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(3, dim)) * 2.0
    X = np.vstack([centers[c] + rng.normal(size=(n_per_class, dim)) * 0.3 for c in range(3)])
    y = np.repeat(np.arange(3), n_per_class).astype(np.int8)
    perm = rng.permutation(len(X))
    return X[perm].astype(np.float32), y[perm]


def test_compute_class_weights_balances_inverse_freq():
    y = np.array([0, 0, 0, 1, 2, 2, 2, 2, 2])
    w = compute_class_weights(y)
    assert w.shape == (3,)
    assert w[1] > w[0] > w[2]  # rarer class gets larger weight


def test_compute_class_weights_handles_empty_class():
    y = np.array([0, 0, 2, 2, 2])
    w = compute_class_weights(y)
    assert w[1] == pytest.approx(0.0)
    assert w[0] > 0 and w[2] > 0


def test_stratified_split_preserves_class_presence():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 3, size=120).astype(np.int8)
    tr, va, te = stratified_split(y, val_fraction=0.15, test_fraction=0.15, seed=0)
    assert set(np.unique(y[tr])) == {0, 1, 2}
    for cls in np.unique(y):
        assert cls in y[tr] or cls in y[va] or cls in y[te]
    assert len(tr) + len(va) + len(te) == len(y)
    assert len(set(tr.tolist()) & set(va.tolist())) == 0
    assert len(set(va.tolist()) & set(te.tolist())) == 0


def test_per_class_metrics_perfect_predictions():
    y = np.array([0, 1, 2, 0, 1, 2])
    report = per_class_metrics(y, y)
    assert report["macro_f1"] == 1.0
    for cls in CLASS_NAMES:
        assert report["per_class"][cls]["f1"] == 1.0


def test_train_model_converges_on_synthetic():
    X, y = _make_synthetic_dataset(n_per_class=40, dim=32, seed=1)
    tr, va, te = stratified_split(y, 0.2, 0.2, seed=1)
    cfg = TrainConfig(hidden_dim=32, epochs=20, seed=1, early_stop_patience=10)
    model, history = train_model(X[tr], y[tr], X[va], y[va], cfg)
    report = evaluate(model, X[te], y[te])
    assert report["macro_f1"] > 0.55, f"macro_f1 too low: {report}"
    rates = report["zero_weight_rate"]
    assert 0.0 <= rates["fc1"] <= 1.0
    assert 0.0 <= rates["fc2"] <= 1.0
    # No NaN crept in
    assert all(not (l != l) for l in history.train_loss)  # NaN != NaN


def test_predict_no_nan():
    X, y = _make_synthetic_dataset(n_per_class=20, dim=16, seed=2)
    tr, va, _ = stratified_split(y, 0.2, 0.2, seed=2)
    cfg = TrainConfig(hidden_dim=16, epochs=3, seed=2)
    model, _ = train_model(X[tr], y[tr], X[va], y[va], cfg)
    preds = predict(model, X)
    assert preds.shape == (len(X),)
    assert set(np.unique(preds).tolist()).issubset({0, 1, 2})


# ═══════════════════════════════════════════════════════════
# LABEL DERIVATION + GRAPH REFINEMENT
# ═══════════════════════════════════════════════════════════

def test_epistemic_from_confidence_bins():
    assert epistemic_from_confidence(0.95) == 2
    assert epistemic_from_confidence(0.60) == 1
    assert epistemic_from_confidence(0.20) == 0
    # boundary checks (matches mcp/nova_shard_db._epistemic_from_confidence)
    assert epistemic_from_confidence(0.85) == 2
    assert epistemic_from_confidence(0.40) == 1
    assert epistemic_from_confidence(0.3999) == 0


def test_base_label_prefers_explicit_epistemic():
    shard = {"meta_tags": {"epistemic": 0, "confidence": 0.95}}
    assert base_label(shard) == 0


def test_base_label_falls_back_to_confidence():
    shard = {"meta_tags": {"confidence": 0.95}}
    assert base_label(shard) == 2


def test_base_label_defaults_to_unknown_when_missing():
    assert base_label({"meta_tags": {}}) == 1
    assert base_label({}) == 1


def test_refine_supersedes_target_becomes_false():
    labels = {"a": 2, "b": 1}
    confs = {"a": 0.9, "b": 0.7}
    rels = [{"source": "a", "target": "b", "type": "supersedes", "reason": "stale"}]
    out, deltas = refine_with_graph(labels, confs, rels)
    assert out["b"] == 0
    assert deltas["supersedes_target_to_0"] == 1


def test_refine_contradicts_demotes_lower_confidence():
    labels = {"hi": 2, "lo": 1}
    confs = {"hi": 0.9, "lo": 0.5}
    rels = [{"source": "lo", "target": "hi", "type": "contradicts"}]
    out, deltas = refine_with_graph(labels, confs, rels)
    assert out["lo"] == 0
    assert out["hi"] == 2
    assert deltas["contradicts_lower_conf_to_0"] == 1


def test_refine_corroborated_promotes_unknown_to_true():
    labels = {"x": 1}
    confs = {"x": 0.7, "y": 0.8}
    rels = [{"source": "x", "target": "y", "type": "corroborated_by"}]
    out, deltas = refine_with_graph(labels, confs, rels)
    assert out["x"] == 2
    assert deltas["corroborated_1_to_2"] == 1


def test_refine_does_not_promote_existing_false():
    labels = {"x": 0}
    confs = {"x": 0.7}
    rels = [{"source": "x", "target": "y", "type": "corroborated_by"}]
    out, deltas = refine_with_graph(labels, confs, rels)
    assert out["x"] == 0
    assert deltas["corroborated_1_to_2"] == 0


# ═══════════════════════════════════════════════════════════
# INPUT TEXT + LEAKAGE ABLATION
# ═══════════════════════════════════════════════════════════

def test_build_input_text_question_only():
    shard = {"guiding_question": "What is the meaning of unit tests?",
             "conversation_history": [{"user": "U", "ai": "A"}]}
    assert build_input_text(shard, mode="question") == "What is the meaning of unit tests?"


def test_build_input_text_question_body():
    shard = {
        "guiding_question": "Q?",
        "conversation_history": [{"user": "user-said", "ai": "ai-answered"}],
    }
    text = build_input_text(shard, mode="question_body")
    assert "Q?" in text
    assert "user-said" in text
    assert "ai-answered" in text


def test_strip_vocab_removes_targets():
    text = "This claim was disproven, then later confirmed by review."
    stripped = strip_vocab(text, EPISTEMIC_VOCAB)
    assert "disproven" not in stripped.lower()
    assert "confirmed" not in stripped.lower()
    assert "claim" in stripped  # untouched content survives


# ═══════════════════════════════════════════════════════════
# END-TO-END BUILD_DATASET ON SYNTHETIC FIXTURES
# ═══════════════════════════════════════════════════════════

def _synthetic_index():
    return {
        "shard_a": {"shard_id": "shard_a"},
        "shard_b": {"shard_id": "shard_b"},
        "shard_c": {"shard_id": "shard_c"},
    }


def _synthetic_shards() -> dict[str, dict]:
    return {
        "shard_a": {
            "shard_id": "shard_a",
            "guiding_question": "alpha facts",
            "meta_tags": {"confidence": 0.95, "epistemic": 2},
            "conversation_history": [{"user": "alpha", "ai": "verified"}],
        },
        "shard_b": {
            "shard_id": "shard_b",
            "guiding_question": "beta unknowns",
            "meta_tags": {"confidence": 0.55, "epistemic": 1},
            "conversation_history": [{"user": "beta", "ai": "maybe"}],
        },
        "shard_c": {
            "shard_id": "shard_c",
            "guiding_question": "gamma falsehoods",
            "meta_tags": {"confidence": 0.10, "epistemic": 0},
            "conversation_history": [{"user": "gamma", "ai": "refuted"}],
        },
    }


def _stub_embed(texts: list[str]) -> np.ndarray:
    """Deterministic stand-in for the sentence-transformer.

    Each text gets a reproducible vector keyed off its length and a digest
    of its content — enough variance for shape tests without depending on
    the real model.
    """
    out = np.zeros((len(texts), 384), dtype=np.float32)
    for i, t in enumerate(texts):
        seed = (abs(hash(t)) + len(t)) % (2**31)
        local = np.random.default_rng(seed)
        out[i] = local.normal(size=(384,)).astype(np.float32)
    return out


def test_build_dataset_end_to_end_with_stubs():
    shards = _synthetic_shards()
    X, y, ids, stats = build_dataset(
        embed_fn=_stub_embed,
        load_index_fn=_synthetic_index,
        load_shard_fn=lambda sid: (shards[sid], f"/tmp/{sid}.md"),
        load_graph_fn=lambda: {"entities": {}, "relations": []},
        refine_with_graph_flag=True,
    )
    assert X.shape == (3, 384)
    assert X.dtype == np.float32
    assert sorted(ids) == ["shard_a", "shard_b", "shard_c"]
    label_map = dict(zip(ids, y.tolist()))
    assert label_map["shard_a"] == 2
    assert label_map["shard_b"] == 1
    assert label_map["shard_c"] == 0
    assert stats["class_counts"] == {"false": 1, "unknown": 1, "true": 1}


def test_build_dataset_applies_graph_refinement():
    shards = _synthetic_shards()
    # shard_b is superseded by shard_a → its label should become 0.
    graph = {
        "entities": {},
        "relations": [
            {"source": "shard_a", "target": "shard_b", "type": "supersedes", "reason": "newer"}
        ],
    }
    X, y, ids, stats = build_dataset(
        embed_fn=_stub_embed,
        load_index_fn=_synthetic_index,
        load_shard_fn=lambda sid: (shards[sid], f"/tmp/{sid}.md"),
        load_graph_fn=lambda: graph,
        refine_with_graph_flag=True,
    )
    label_map = dict(zip(ids, y.tolist()))
    assert label_map["shard_b"] == 0
    assert stats["refinement_deltas"]["supersedes_target_to_0"] == 1
