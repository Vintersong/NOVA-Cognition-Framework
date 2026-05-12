"""
train_ternary_net.py — Train the TernaryNet classifier on a NOVA dataset.

Reads `output/ternary_net/dataset.npz` (produced by
`utilities/export_ternary_dataset.py`), runs a stratified train/val/test
split, trains a TernaryNet with class-weighted cross-entropy, and writes:

    output/ternary_net/checkpoint.pt
    output/ternary_net/eval_report.json
    output/ternary_net/sensitivity_thresholds.json   (only with --threshold-sweep)

Usage:
    # Baseline run
    python utilities/train_ternary_net.py

    # Hidden-size ablation
    python utilities/train_ternary_net.py --hidden 32
    python utilities/train_ternary_net.py --hidden 128

    # Threshold-sensitivity sweep (re-derives labels from raw shards)
    python utilities/train_ternary_net.py --threshold-sweep

PoC reminder: this script is read-only against NOVA. The model is saved to
disk; nothing is written back to shards.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
UTIL_DIR = REPO_ROOT / "utilities"
for p in (MCP_DIR, UTIL_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

DEFAULT_DATASET = REPO_ROOT / "output" / "ternary_net" / "dataset.npz"
DEFAULT_OUT_DIR = REPO_ROOT / "output" / "ternary_net"

logger = logging.getLogger(__name__)


def _train_one(
    X: np.ndarray,
    y: np.ndarray,
    hidden: int,
    epochs: int,
    seed: int,
    val_fraction: float,
    test_fraction: float,
    early_stop_patience: int,
) -> tuple[Any, dict, dict]:
    """Single train/eval pass. Returns (model, eval_report, train_history)."""
    from ternary_net import (
        TrainConfig,
        evaluate,
        stratified_split,
        train_model,
    )

    cfg = TrainConfig(
        hidden_dim=hidden,
        epochs=epochs,
        seed=seed,
        val_fraction=val_fraction,
        test_fraction=test_fraction,
        early_stop_patience=early_stop_patience,
    )
    tr, va, te = stratified_split(y, val_fraction, test_fraction, seed)
    if len(tr) < 2 or len(va) < 1 or len(te) < 1:
        raise RuntimeError(
            f"Dataset too small to split: n_train={len(tr)} n_val={len(va)} n_test={len(te)}. "
            "Need more shards."
        )
    X_tr, y_tr = X[tr], y[tr]
    X_va, y_va = X[va], y[va]
    X_te, y_te = X[te], y[te]

    model, history = train_model(X_tr, y_tr, X_va, y_va, cfg)
    report = evaluate(model, X_te, y_te)
    report["n_train"] = int(len(tr))
    report["n_val"] = int(len(va))
    report["n_test"] = int(len(te))
    report["hidden"] = int(hidden)
    report["seed"] = int(seed)
    report["best_epoch"] = int(history.best_epoch)
    report["best_val_macro_f1"] = float(history.best_val_macro_f1)
    return model, report, {
        "train_loss": history.train_loss,
        "val_macro_f1": history.val_macro_f1,
        "zero_weight_rates": history.zero_weight_rates,
    }


def run_baseline(
    dataset_path: Path,
    out_dir: Path,
    hidden: int,
    epochs: int,
    seed: int,
    val_fraction: float,
    test_fraction: float,
    early_stop_patience: int,
) -> dict:
    """Train and write checkpoint + eval_report."""
    from export_ternary_dataset import load_dataset
    from ternary_net import save_checkpoint

    X, y, ids = load_dataset(dataset_path)
    model, report, history = _train_one(
        X, y, hidden, epochs, seed, val_fraction, test_fraction, early_stop_patience
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = out_dir / "checkpoint.pt"
    save_checkpoint(
        model,
        meta={
            "input_dim": int(X.shape[1]),
            "hidden_dim": int(hidden),
            "num_classes": 3,
            "seed": int(seed),
            "best_val_macro_f1": float(report["best_val_macro_f1"]),
            "dataset_path": str(dataset_path),
            "n_train": report["n_train"],
        },
        path=ckpt,
    )

    report["history"] = history
    (out_dir / "eval_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def run_threshold_sweep(
    out_dir: Path,
    hidden: int,
    epochs: int,
    seed: int,
    val_fraction: float,
    test_fraction: float,
    early_stop_patience: int,
    shard_dir: str | None,
    text_mode: str,
    refine_with_graph: bool,
) -> dict:
    """Re-derive labels at alternate threshold edges and report F1 deltas.

    The sweep tells us how much of any reported F1 is an artifact of NOVA's
    specific (0.40, 0.85) cutoffs vs. a learnable signal. If the swing is
    large (>0.10), the labels are threshold-dominated and the model should
    be regarded as a threshold-classifier, not a semantic one.
    """
    from export_ternary_dataset import build_dataset

    edges = [(0.30, 0.80), (0.40, 0.85), (0.50, 0.90)]
    results = {}
    for lo, hi in edges:
        X, y, ids, stats = build_dataset(
            shard_dir=shard_dir,
            text_mode=text_mode,
            refine_with_graph_flag=refine_with_graph,
            low_threshold=lo,
            high_threshold=hi,
            seed=seed,
        )
        try:
            _, report, _ = _train_one(
                X, y, hidden, epochs, seed, val_fraction, test_fraction, early_stop_patience
            )
        except RuntimeError as exc:
            results[f"{lo}_{hi}"] = {"error": str(exc), "class_counts": stats["class_counts"]}
            continue
        results[f"{lo}_{hi}"] = {
            "macro_f1": report["macro_f1"],
            "per_class": report["per_class"],
            "class_counts": stats["class_counts"],
        }

    f1s = [v["macro_f1"] for v in results.values() if "macro_f1" in v]
    swing = round(max(f1s) - min(f1s), 4) if len(f1s) >= 2 else None
    payload = {
        "edges_tried": [f"{lo}_{hi}" for lo, hi in edges],
        "results": results,
        "macro_f1_swing": swing,
        "interpretation": (
            "swing > 0.10 means labels are threshold-dominated; "
            "swing <= 0.10 means the model has learned signal beyond the bin edges."
            if swing is not None else "insufficient runs to interpret"
        ),
        "hidden": hidden,
        "seed": seed,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sensitivity_thresholds.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train TernaryNet on a NOVA-derived dataset.")
    p.add_argument("--dataset", default=str(DEFAULT_DATASET),
                   help="Input dataset.npz (default: %(default)s).")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--seed", type=int, default=1729)
    p.add_argument("--val-fraction", type=float, default=0.15)
    p.add_argument("--test-fraction", type=float, default=0.15)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--threshold-sweep", action="store_true",
                   help="Run the threshold-sensitivity sweep instead of a single training run.")
    p.add_argument("--shard-dir", default=None,
                   help="Used only with --threshold-sweep to re-derive labels.")
    p.add_argument("--text-mode", choices=["question", "question_body"], default="question_body")
    p.add_argument("--no-graph-refine", dest="refine", action="store_false", default=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    out_dir = Path(args.out_dir)

    if args.threshold_sweep:
        payload = run_threshold_sweep(
            out_dir=out_dir,
            hidden=args.hidden,
            epochs=args.epochs,
            seed=args.seed,
            val_fraction=args.val_fraction,
            test_fraction=args.test_fraction,
            early_stop_patience=args.patience,
            shard_dir=args.shard_dir,
            text_mode=args.text_mode,
            refine_with_graph=args.refine,
        )
        print(json.dumps({"sweep": payload["results"], "swing": payload["macro_f1_swing"]}, indent=2))
        return 0

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Dataset not found at {dataset_path}. "
              f"Run utilities/export_ternary_dataset.py first.", file=sys.stderr)
        return 2

    report = run_baseline(
        dataset_path=dataset_path,
        out_dir=out_dir,
        hidden=args.hidden,
        epochs=args.epochs,
        seed=args.seed,
        val_fraction=args.val_fraction,
        test_fraction=args.test_fraction,
        early_stop_patience=args.patience,
    )
    print(json.dumps({
        "macro_f1": report["macro_f1"],
        "per_class": report["per_class"],
        "zero_weight_rate": report["zero_weight_rate"],
        "best_epoch": report["best_epoch"],
        "n_train": report["n_train"],
        "n_val": report["n_val"],
        "n_test": report["n_test"],
    }, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
