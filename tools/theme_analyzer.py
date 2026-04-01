"""
theme_analyzer.py — Cluster NOVA shards by semantic theme and auto-tag them

Uses sklearn for K-means clustering and TF-IDF fallback when shard embeddings
are absent.  Writes derived theme labels back to each shard (unless --dry-run).

Usage:
    python tools/theme_analyzer.py [options]

Options:
    --shard-dir           Path to shard directory (default: nova_memory/ at repo root)
    --n-clusters          Number of theme clusters (default: 8)
    --dry-run             Print proposed theme assignments without writing anything
    --export-cluster-map  Write nova_cluster_map.json into --shard-dir summarizing
                          the cluster structure

Examples:
    # Preview cluster assignments:
    python tools/theme_analyzer.py --dry-run

    # Cluster and tag shards in a custom directory with 5 clusters:
    python tools/theme_analyzer.py --shard-dir ./shards --n-clusters 5

    # Cluster, tag, and export the cluster map:
    python tools/theme_analyzer.py --export-cluster-map
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# sklearn is a required dependency for this script.  Provide a clear error
# message if it is not available rather than a raw ImportError traceback.
# ---------------------------------------------------------------------------
try:
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
except ImportError:
    print(
        "ERROR: scikit-learn is required by theme_analyzer.py.\n"
        "Install it with:  pip install scikit-learn\n"
        "(It is already listed in mcp/requirements.txt via sentence-transformers.)"
    )
    sys.exit(1)


# ═══════════════════════════════════════════════════════════
# STOP-WORD LIST  (minimal English set — no NLTK dependency)
# ═══════════════════════════════════════════════════════════

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "not", "no", "nor",
    "so", "yet", "both", "either", "neither", "each", "few", "more", "most",
    "other", "some", "such", "than", "too", "very", "just", "how", "what",
    "when", "where", "who", "which", "that", "this", "these", "those", "i",
    "we", "you", "he", "she", "it", "they", "me", "us", "him", "her", "them",
    "my", "our", "your", "his", "its", "their", "about", "into", "through",
    "during", "before", "after", "above", "below", "between", "up", "down",
    "out", "off", "over", "under", "again", "then", "once", "here", "there",
}


# ═══════════════════════════════════════════════════════════
# SHARD LOADING
# ═══════════════════════════════════════════════════════════

def load_shards(shard_dir: Path) -> list[tuple[Path, dict]]:
    """Return a list of (path, shard_data) for every .json file in shard_dir."""
    results = []
    for path in sorted(shard_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.append((path, data))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  WARNING: could not load {path.name}: {exc}", file=sys.stderr)
    return results


# ═══════════════════════════════════════════════════════════
# EMBEDDING / FEATURE MATRIX
# ═══════════════════════════════════════════════════════════

def build_feature_matrix(shards: list[tuple[Path, dict]]):
    """
    Return (matrix, used_embeddings) where matrix is a 2-D array-like of shape
    (n_shards, n_features).

    Strategy:
      1. If every shard has a stored embedding (in shard["context"]["embedding"]),
         stack them into a numpy array — fastest and most accurate.
      2. If *any* shard is missing an embedding, fall back to TF-IDF over
         guiding_question + intent for ALL shards so the feature dimensions match.
    """
    import numpy as np

    vecs = []
    for _, shard in shards:
        emb = shard.get("context", {}).get("embedding")
        if emb and isinstance(emb, list):
            vecs.append(emb)
        else:
            vecs.append(None)

    if all(v is not None for v in vecs):
        return np.array(vecs, dtype=float), True

    # TF-IDF fallback
    texts = []
    for _, shard in shards:
        question = shard.get("guiding_question", "")
        intent = shard.get("meta_tags", {}).get("intent", "")
        texts.append(f"{question} {intent}".strip() or "unknown")

    vectorizer = TfidfVectorizer(stop_words="english", min_df=1)
    matrix = vectorizer.fit_transform(texts)
    return matrix, False


# ═══════════════════════════════════════════════════════════
# THEME LABEL DERIVATION
# ═══════════════════════════════════════════════════════════

def derive_theme_label(shards_in_cluster: list[dict]) -> str:
    """
    Derive a human-readable theme label for a cluster.

    Takes the 3 most common significant words across all guiding_question
    fields in the cluster (stop-word filtered, lower-cased).
    """
    words: list[str] = []
    for shard in shards_in_cluster:
        question = shard.get("guiding_question", "")
        for token in question.lower().split():
            # Strip punctuation from boundaries
            token = token.strip(".,!?\"'():;-")
            if token and token not in _STOP_WORDS and len(token) > 2:
                words.append(token)

    if not words:
        return "general"

    top3 = [word for word, _ in Counter(words).most_common(3)]
    return "_".join(top3)


# ═══════════════════════════════════════════════════════════
# MAIN ANALYSIS
# ═══════════════════════════════════════════════════════════

def run_analysis(
    shards: list[tuple[Path, dict]],
    n_clusters: int,
    dry_run: bool,
    export_cluster_map: bool,
    shard_dir: Path,
) -> None:
    import numpy as np

    n_clusters = min(n_clusters, len(shards))

    print(f"Building feature matrix for {len(shards)} shards …")
    matrix, used_embeddings = build_feature_matrix(shards)
    feature_source = "stored embeddings" if used_embeddings else "TF-IDF (fallback)"
    print(f"Feature source: {feature_source}")

    print(f"Running K-means with {n_clusters} clusters …")
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init="auto")
    labels = kmeans.fit_predict(matrix)

    # Group shards by cluster
    clusters: dict[int, list[tuple[Path, dict]]] = {}
    for (path, shard), cluster_id in zip(shards, labels):
        clusters.setdefault(int(cluster_id), []).append((path, shard))

    # Derive theme label per cluster
    cluster_info: list[dict] = []
    for cluster_id in sorted(clusters.keys()):
        members = clusters[cluster_id]
        shard_datas = [s for _, s in members]
        label = derive_theme_label(shard_datas)
        shard_ids = [s.get("shard_id", p.stem) for p, s in members]
        cluster_info.append({
            "id": cluster_id,
            "label": label,
            "shard_ids": shard_ids,
            "size": len(members),
        })

    # Print cluster assignments
    print("\nCluster assignments:")
    print("-" * 60)
    for info in cluster_info:
        print(f"  Cluster {info['id']:>2}  [{info['label']}]  ({info['size']} shards)")
        for sid in info["shard_ids"]:
            print(f"           • {sid}")
    print("-" * 60)

    if dry_run:
        print("\n[DRY RUN] No shards were modified.")
    else:
        # Write theme labels back to each shard
        updated = 0
        for info in cluster_info:
            label = info["label"]
            cluster_id = info["id"]
            for path, shard in clusters[cluster_id]:
                meta = shard.setdefault("meta_tags", {})
                meta["theme"] = label
                shard["last_modified"] = datetime.now(tz=timezone.utc).isoformat()
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(shard, f, indent=2, ensure_ascii=False)
                    updated += 1
                except OSError as exc:
                    print(
                        f"  WARNING: could not write {path.name}: {exc}",
                        file=sys.stderr,
                    )
        print(f"\nUpdated theme tags on {updated} shard(s).")

    # Export cluster map if requested
    if export_cluster_map:
        cluster_map = {
            "shard_id": "nova-cluster-map",
            "theme": "meta",
            "clusters": cluster_info,
        }
        map_path = shard_dir / "nova_cluster_map.json"
        if dry_run:
            print(f"\n[DRY RUN] Would write cluster map to: {map_path}")
        else:
            try:
                with open(map_path, "w", encoding="utf-8") as f:
                    json.dump(cluster_map, f, indent=2, ensure_ascii=False)
                print(f"\nCluster map written to: {map_path}")
            except OSError as exc:
                print(f"ERROR: could not write cluster map: {exc}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

def main() -> int:
    repo_root = Path(__file__).parent.parent
    default_shard_dir = repo_root / "nova_memory"

    parser = argparse.ArgumentParser(
        description="Cluster NOVA shards by semantic theme and auto-tag them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--shard-dir",
        default=str(default_shard_dir),
        help="Path to shard directory (default: nova_memory/ at repo root)",
    )
    parser.add_argument(
        "--n-clusters",
        type=int,
        default=8,
        help="Number of theme clusters (default: 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed theme assignments without writing anything",
    )
    parser.add_argument(
        "--export-cluster-map",
        action="store_true",
        help="Write nova_cluster_map.json into --shard-dir",
    )
    args = parser.parse_args()

    shard_dir = Path(args.shard_dir)

    if not shard_dir.exists():
        print(f"Shard directory not found: {shard_dir}  (nothing to do)")
        return 0

    shards = load_shards(shard_dir)
    if not shards:
        print(f"No shard files found in {shard_dir}  (nothing to do)")
        return 0

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Theme Analyzer — {shard_dir}")
    print(f"Shards: {len(shards)}  |  Clusters: {args.n_clusters}")

    run_analysis(
        shards=shards,
        n_clusters=args.n_clusters,
        dry_run=args.dry_run,
        export_cluster_map=args.export_cluster_map,
        shard_dir=shard_dir,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
