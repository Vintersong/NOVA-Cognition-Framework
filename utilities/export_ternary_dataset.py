"""
export_ternary_dataset.py — Turn NOVA shards into a ternary-labeled dataset.

PoC scope: read-only against NOVA. Iterates shards via `store.load_index` /
`store.load_shard`, embeds each shard's text with the existing
`all-MiniLM-L6-v2` model, derives a ternary label {0=false, 1=unknown,
2=true} from the shard's epistemic state, and writes:

    output/ternary_net/dataset.npz          # X, y, ids
    output/ternary_net/label_stats.json     # class counts + refinement deltas

Graph refinement (optional, on by default): adjusts the base label using
`supersedes`, `contradicts`, and `corroborated_by` edges from
`mcp/graph.py`. The directionality follows `graph.add_supersedes(source,
target, reason)` meaning "source replaces target" — so a shard that is the
*target* of a supersedes edge is relabeled to 0.

Usage:
    python utilities/export_ternary_dataset.py \\
        --out output/ternary_net/dataset.npz \\
        [--text-mode question_body|question] \\
        [--no-graph-refine] [--strip-vocab] \\
        [--low-threshold 0.40 --high-threshold 0.85] \\
        [--shard-dir /path/to/shards] [--seed 1729]

Tests stub the embedder by importing `build_dataset` directly with an
`embed_fn` argument so they don't have to download the model.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Literal

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

logger = logging.getLogger(__name__)

TextMode = Literal["question", "question_body"]
DEFAULT_OUT = REPO_ROOT / "output" / "ternary_net" / "dataset.npz"
DEFAULT_STATS = REPO_ROOT / "output" / "ternary_net" / "label_stats.json"

# Words removed in the lexical-leakage ablation. Kept small and explicit so
# the ablation is auditable and so we don't accidentally strip semantic
# content unrelated to epistemic status.
EPISTEMIC_VOCAB: tuple[str, ...] = (
    "confirmed", "contradicted", "disproven", "verified", "superseded",
    "refuted", "falsified", "corroborated", "debunked", "unknown",
    "uncertain", "low confidence", "high confidence",
)


# ═══════════════════════════════════════════════════════════
# TEXT CONSTRUCTION
# ═══════════════════════════════════════════════════════════

def _body_text_from_shard(shard: dict, max_chars: int = 512) -> str:
    """Concatenate the first user/AI turns into a short body excerpt."""
    parts: list[str] = []
    summary = shard.get("context", {}).get("summary") or shard.get("summary") or ""
    if summary:
        parts.append(summary)
    for turn in shard.get("conversation_history", []) or []:
        if turn.get("user"):
            parts.append(str(turn["user"]))
        if turn.get("ai"):
            parts.append(str(turn["ai"]))
        joined = " ".join(parts)
        if len(joined) >= max_chars:
            return joined[:max_chars]
    return " ".join(parts)[:max_chars]


def build_input_text(
    shard: dict,
    mode: TextMode = "question_body",
    max_body_chars: int = 512,
    strip_epistemic_vocab: bool = False,
) -> str:
    q = (shard.get("guiding_question") or "").strip()
    if mode == "question":
        text = q
    else:
        body = _body_text_from_shard(shard, max_chars=max_body_chars)
        text = (q + "\n\n" + body).strip() if body else q
    if strip_epistemic_vocab:
        text = strip_vocab(text, EPISTEMIC_VOCAB)
    return text


def strip_vocab(text: str, words: Iterable[str]) -> str:
    """Case-insensitive whole-word removal. Used for leakage ablation only."""
    for w in words:
        pattern = re.compile(rf"\b{re.escape(w)}\b", flags=re.IGNORECASE)
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# ═══════════════════════════════════════════════════════════
# LABEL DERIVATION
# ═══════════════════════════════════════════════════════════

def base_label(
    shard: dict,
    low_threshold: float = 0.40,
    high_threshold: float = 0.85,
) -> int:
    """Pick the most authoritative ternary label NOVA already has for the shard.

    Order: explicit `meta_tags.epistemic` → derived from confidence at the
    given bin edges. Defaults to 1 (unknown) when neither is present.
    """
    from ternary_net import epistemic_from_confidence

    meta = shard.get("meta_tags") or {}
    if "epistemic" in meta and meta["epistemic"] is not None:
        try:
            return int(meta["epistemic"])
        except (TypeError, ValueError):
            pass
    if "confidence" in meta:
        try:
            return epistemic_from_confidence(
                float(meta["confidence"]),
                low_threshold=low_threshold,
                high_threshold=high_threshold,
            )
        except (TypeError, ValueError):
            pass
    return 1


def refine_with_graph(
    base_labels: dict[str, int],
    confidences: dict[str, float],
    relations: list[dict],
) -> tuple[dict[str, int], dict[str, int]]:
    """Apply supersedes / contradicts / corroborated_by edges to refine labels.

    Rules:
      * shard is target of a `supersedes` edge   → label 0 (it was replaced)
      * shard is on a `contradicts` edge AND its confidence is below the
        peer's                                   → label 0 (peer won the dispute)
      * shard has incoming `corroborated_by`     → upgrade 1 → 2

    The current `epistemic=0` and `epistemic=2` labels are sticky: we never
    move a shard *out* of `false` or `true` because of an edge alone — the
    base label already reflects what NÓTT decided.

    Returns the new labels dict and a delta dict counting each transition.
    """
    labels = dict(base_labels)
    deltas: dict[str, int] = {
        "supersedes_target_to_0": 0,
        "contradicts_lower_conf_to_0": 0,
        "corroborated_1_to_2": 0,
        "ties": 0,
    }

    for rel in relations:
        rtype = rel.get("type")
        src, tgt = rel.get("source"), rel.get("target")
        if not src or not tgt:
            continue

        if rtype == "supersedes" and tgt in labels and labels[tgt] != 0:
            labels[tgt] = 0
            deltas["supersedes_target_to_0"] += 1

        elif rtype == "contradicts" and src in labels and tgt in labels:
            cs, ct = confidences.get(src, 1.0), confidences.get(tgt, 1.0)
            if cs < ct and labels[src] != 0:
                labels[src] = 0
                deltas["contradicts_lower_conf_to_0"] += 1
            elif ct < cs and labels[tgt] != 0:
                labels[tgt] = 0
                deltas["contradicts_lower_conf_to_0"] += 1
            else:
                deltas["ties"] += 1

        elif rtype == "corroborated_by" and src in labels and labels[src] == 1:
            labels[src] = 2
            deltas["corroborated_1_to_2"] += 1

    return labels, deltas


# ═══════════════════════════════════════════════════════════
# EMBEDDING
# ═══════════════════════════════════════════════════════════

def _default_embed_fn(texts: list[str], batch_size: int = 64) -> np.ndarray:
    """Embed with NOVA's local sentence-transformers model.

    Returns a (N, 384) float32 array. Raises if sentence-transformers isn't
    installed; the export utility cannot proceed without embeddings.
    """
    from nova_embeddings_local import get_embedding_model  # type: ignore

    model = get_embedding_model()
    if model is None:
        raise RuntimeError(
            "sentence-transformers is not available. "
            "Install via: pip install -r mcp/requirements.txt"
        )
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return np.asarray(vectors, dtype=np.float32)


# ═══════════════════════════════════════════════════════════
# MAIN BUILDER
# ═══════════════════════════════════════════════════════════

def build_dataset(
    shard_dir: str | Path | None = None,
    text_mode: TextMode = "question_body",
    refine_with_graph_flag: bool = True,
    strip_epistemic_vocab: bool = False,
    low_threshold: float = 0.40,
    high_threshold: float = 0.85,
    embed_fn: Callable[[list[str]], np.ndarray] | None = None,
    load_index_fn: Callable[[], dict] | None = None,
    load_shard_fn: Callable[[str], tuple[dict, str]] | None = None,
    load_graph_fn: Callable[[], dict] | None = None,
    seed: int = 1729,
) -> tuple[np.ndarray, np.ndarray, list[str], dict]:
    """Read shards, embed text, derive labels, optionally refine via graph.

    The `*_fn` arguments exist so tests can inject fixtures without monkey-
    patching the NOVA singletons. In production all four default to the
    canonical store/graph/embeddings calls.

    Returns (X, y, ids, stats) where X is (N, 384) float32, y is (N,) int8,
    ids is the list of N shard IDs, and stats is the label_stats dict.
    """
    if shard_dir is not None:
        os.environ["NOVA_SHARD_DIR"] = str(shard_dir)

    if load_index_fn is None or load_shard_fn is None:
        from store import load_index as _load_index, load_shard as _load_shard
        load_index_fn = load_index_fn or _load_index
        load_shard_fn = load_shard_fn or _load_shard

    if load_graph_fn is None:
        from graph import load_graph as _load_graph
        load_graph_fn = _load_graph

    if embed_fn is None:
        embed_fn = _default_embed_fn

    index = load_index_fn()
    shard_ids = sorted(index.keys())
    if not shard_ids:
        raise RuntimeError(
            "No shards found via store.load_index(). "
            "Set NOVA_SHARD_DIR or pass --shard-dir to a populated directory."
        )

    texts: list[str] = []
    ids: list[str] = []
    raw_labels: dict[str, int] = {}
    confidences: dict[str, float] = {}

    for sid in shard_ids:
        try:
            shard, _ = load_shard_fn(sid)
        except Exception as exc:
            logger.warning("export_ternary_dataset: skipping %s (%s)", sid, exc)
            continue
        text = build_input_text(
            shard,
            mode=text_mode,
            strip_epistemic_vocab=strip_epistemic_vocab,
        )
        if not text:
            continue
        label = base_label(shard, low_threshold=low_threshold, high_threshold=high_threshold)
        confidence = float((shard.get("meta_tags") or {}).get("confidence", 1.0))

        texts.append(text)
        ids.append(sid)
        raw_labels[sid] = label
        confidences[sid] = confidence

    if not ids:
        raise RuntimeError("All shards yielded empty text. Nothing to export.")

    refinement_deltas: dict[str, int] = {}
    if refine_with_graph_flag:
        graph = load_graph_fn() or {}
        relations = graph.get("relations") or []
        refined, refinement_deltas = refine_with_graph(raw_labels, confidences, relations)
        labels = refined
    else:
        labels = raw_labels

    X = embed_fn(texts)
    if X.shape[0] != len(ids):
        raise RuntimeError(
            f"Embed function returned {X.shape[0]} rows for {len(ids)} texts."
        )
    y = np.array([labels[sid] for sid in ids], dtype=np.int8)

    counts = np.bincount(y.astype(np.int64), minlength=3).tolist()
    stats = {
        "n_shards": len(ids),
        "class_counts": {
            "false": int(counts[0]),
            "unknown": int(counts[1]),
            "true": int(counts[2]),
        },
        "text_mode": text_mode,
        "strip_epistemic_vocab": strip_epistemic_vocab,
        "refine_with_graph": refine_with_graph_flag,
        "low_threshold": low_threshold,
        "high_threshold": high_threshold,
        "embedding_dim": int(X.shape[1]),
        "refinement_deltas": refinement_deltas,
        "seed": seed,
    }
    return X.astype(np.float32), y, ids, stats


# ═══════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════

def save_dataset(
    X: np.ndarray,
    y: np.ndarray,
    ids: list[str],
    stats: dict,
    out_path: str | Path,
    stats_path: str | Path | None = None,
) -> tuple[Path, Path]:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, X=X, y=y, ids=np.array(ids, dtype=object))
    stats_p = Path(stats_path) if stats_path else out.with_name("label_stats.json")
    stats_p.parent.mkdir(parents=True, exist_ok=True)
    stats_p.write_text(json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8")
    return out, stats_p


def load_dataset(path: str | Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    obj = np.load(path, allow_pickle=True)
    X = obj["X"].astype(np.float32)
    y = obj["y"].astype(np.int64)
    ids = [str(s) for s in obj["ids"].tolist()]
    return X, y, ids


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export NOVA shards as ternary-labeled dataset.")
    p.add_argument("--shard-dir", default=None,
                   help="Override NOVA_SHARD_DIR for this run.")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help="Output .npz path. label_stats.json is written next to it.")
    p.add_argument("--text-mode", choices=["question", "question_body"], default="question_body")
    p.add_argument("--no-graph-refine", dest="refine", action="store_false", default=True,
                   help="Skip graph-edge label refinement.")
    p.add_argument("--strip-vocab", action="store_true", default=False,
                   help="Strip epistemic vocabulary from inputs (leakage ablation).")
    p.add_argument("--low-threshold", type=float, default=0.40)
    p.add_argument("--high-threshold", type=float, default=0.85)
    p.add_argument("--seed", type=int, default=1729)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)

    X, y, ids, stats = build_dataset(
        shard_dir=args.shard_dir,
        text_mode=args.text_mode,
        refine_with_graph_flag=args.refine,
        strip_epistemic_vocab=args.strip_vocab,
        low_threshold=args.low_threshold,
        high_threshold=args.high_threshold,
        seed=args.seed,
    )
    out, stats_p = save_dataset(X, y, ids, stats, args.out)

    print(f"Wrote dataset: {out}  (shape={X.shape}, dtype={X.dtype})")
    print(f"Wrote stats:   {stats_p}")
    print(f"Class counts:  {stats['class_counts']}")
    if stats["refinement_deltas"]:
        print(f"Refinements:   {stats['refinement_deltas']}")
    for cls, n in stats["class_counts"].items():
        if 0 < n < 20:
            print(f"WARNING: class '{cls}' has only {n} examples — training may be unstable.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
