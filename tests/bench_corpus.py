"""
bench_corpus.py — Synthetic corpus + instrumentation helpers for the
retrieval pipeline benchmark.

Imported by tests/bench_retrieval.py. Not a test module on its own
(filename doesn't match pytest's collection pattern).

Builds shard indexes with ground-truth cluster labels so recall@k is
computable, plus a synthetic knowledge graph dense enough to trigger
spreading activation. Provides a deterministic Anthropic stub and
embedding stub so the bench is fully offline.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


# ── Per-cluster vocabularies ──────────────────────────────────────────────────
# Each cluster gets a disjoint topic vocab so HUGINN's token overlap has
# real signal to find. Noise shards borrow from a neighbouring cluster's
# vocab but keep the original cluster_id — these are the recall traps.
CLUSTER_VOCAB: list[list[str]] = [
    ["kafka", "broker", "partition", "offset", "consumer", "topic", "log",
     "replication", "producer", "stream", "throughput", "lag", "rebalance",
     "leader", "isr", "compaction", "retention", "segment", "controller", "zookeeper"],
    ["mitochondria", "atp", "ribosome", "membrane", "cytoplasm", "nucleus",
     "enzyme", "protein", "synthesis", "organelle", "chloroplast", "vesicle",
     "lysosome", "cell", "respiration", "metabolism", "glucose", "krebs",
     "electron", "transport"],
    ["sonnet", "stanza", "rhyme", "meter", "iamb", "couplet", "volta",
     "octave", "sestet", "quatrain", "verse", "trochee", "spondee", "elegy",
     "ode", "ballad", "blank", "free", "enjambment", "caesura"],
    ["alloy", "tensile", "yield", "stress", "strain", "ductile", "brittle",
     "fatigue", "creep", "hardness", "machining", "casting", "forging",
     "welding", "annealing", "tempering", "austenite", "martensite",
     "carbide", "steel"],
    ["sonar", "bathymetry", "trench", "abyssal", "pelagic", "benthic",
     "thermocline", "halocline", "salinity", "current", "gyre", "tide",
     "upwelling", "subduction", "ridge", "seamount", "plankton", "krill",
     "cetacean", "demersal"],
]

# Cluster labels — c0, c1, c2, ... Stable across runs.
CLUSTER_IDS: list[str] = [f"c{i}" for i in range(len(CLUSTER_VOCAB))]

EMBEDDING_DIM = 384


def _deterministic_embedding(text: str, cluster_id: str) -> list[float]:
    """
    Build a 384-d unit vector where shards in the same cluster cluster
    together in vector space, with small per-shard jitter for variety.

    The seed is `cluster_id` (so cluster cohesion holds) plus the SHA1 of
    the text (so individual shards differ within the cluster). Not crypto.
    """
    cluster_seed = int(hashlib.sha1(cluster_id.encode()).hexdigest()[:8], 16)
    text_seed = int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)
    rng_cluster = random.Random(cluster_seed)
    rng_text = random.Random(text_seed)
    vec = [
        rng_cluster.gauss(0.0, 1.0) + 0.15 * rng_text.gauss(0.0, 1.0)
        for _ in range(EMBEDDING_DIM)
    ]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _resize_clusters(n_shards: int) -> tuple[list[list[str]], list[str]]:
    """Repeat-and-trim the vocab list when more shards are requested than
    the base 5 clusters can hold at ~30 shards each."""
    target = max(5, math.ceil(n_shards / 30))
    if target <= len(CLUSTER_VOCAB):
        return CLUSTER_VOCAB[:target], CLUSTER_IDS[:target]
    vocabs: list[list[str]] = []
    ids: list[str] = []
    for i in range(target):
        base = CLUSTER_VOCAB[i % len(CLUSTER_VOCAB)]
        # Suffix each term so cross-cluster collisions can't happen
        suffix = f"_{i // len(CLUSTER_VOCAB)}" if i >= len(CLUSTER_VOCAB) else ""
        vocabs.append([f"{w}{suffix}" for w in base] if suffix else base)
        ids.append(f"c{i}")
    return vocabs, ids


@dataclass
class Corpus:
    index: dict
    queries: list[tuple[str, str]]   # (query, expected_cluster_id)
    graph: dict
    shard_dir: Path


def build_corpus(
    tmp_path: Path,
    n_shards: int,
    n_queries_per_cluster: int = 4,
    noise_ratio: float = 0.12,
) -> Corpus:
    """
    Build a synthetic corpus on disk + in memory.

    Returns a Corpus with:
      - index: shard_id -> entry dict (shape matches mcp/test_recall.py:18)
      - queries: list of (query_string, expected_cluster_id)
      - graph: {"entities": {...}, "relations": [...]} (>=10 relations so
        spreading activation runs)
      - shard_dir: tmp_path/shards (each shard written as JSON)
    """
    vocabs, cluster_ids = _resize_clusters(n_shards)
    n_clusters = len(cluster_ids)
    per_cluster = max(1, n_shards // n_clusters)
    n_on_topic = max(1, round(per_cluster * (1 - noise_ratio)))
    n_noise = per_cluster - n_on_topic

    shard_dir = tmp_path / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)

    index: dict = {}
    entities: dict = {}
    relations: list[dict] = []

    rng = random.Random(42)  # deterministic across runs

    for ci, cluster_id in enumerate(cluster_ids):
        on_vocab = vocabs[ci]
        noise_vocab = vocabs[(ci + 1) % n_clusters]
        cluster_shard_ids: list[str] = []

        for j in range(n_on_topic):
            sid = f"{cluster_id}_s{j:03d}"
            terms = rng.sample(on_vocab, k=min(5, len(on_vocab)))
            _write_shard(shard_dir, index, entities, sid, cluster_id, terms)
            cluster_shard_ids.append(sid)

        for j in range(n_noise):
            sid = f"{cluster_id}_n{j:03d}"
            # Noise: borrow neighbour's terms but tag with this cluster.
            terms = rng.sample(noise_vocab, k=min(5, len(noise_vocab)))
            _write_shard(shard_dir, index, entities, sid, cluster_id, terms)
            cluster_shard_ids.append(sid)

        # Wire ~3 same-cluster `references` edges per on-topic shard.
        for sid in cluster_shard_ids[:n_on_topic]:
            siblings = [s for s in cluster_shard_ids if s != sid]
            if not siblings:
                continue
            for target in rng.sample(siblings, k=min(3, len(siblings))):
                relations.append({"source": sid, "target": target, "type": "references"})

    # Queries: pick 3 terms from each cluster's on_vocab.
    queries: list[tuple[str, str]] = []
    for ci, cluster_id in enumerate(cluster_ids):
        on_vocab = vocabs[ci]
        for _ in range(n_queries_per_cluster):
            terms = rng.sample(on_vocab, k=min(3, len(on_vocab)))
            queries.append((" ".join(terms), cluster_id))

    graph = {"entities": entities, "relations": relations}
    return Corpus(index=index, queries=queries, graph=graph, shard_dir=shard_dir)


def _write_shard(
    shard_dir: Path,
    index: dict,
    entities: dict,
    sid: str,
    cluster_id: str,
    terms: list[str],
) -> None:
    """Write one shard JSON to disk and register it in the in-memory index."""
    guiding_q = f"What about {' '.join(terms[:2])}?"
    summary = f"Notes on {' and '.join(terms)}."
    searchable = guiding_q + " " + summary
    embedding = _deterministic_embedding(searchable, cluster_id)

    # On-disk JSON — MUNINN._local_rerank reads `context.embedding` from this.
    shard_data = {
        "shard_id": sid,
        "guiding_question": guiding_q,
        "meta_tags": {
            "confidence": 1.0,
            "cluster_id": cluster_id,
            "summary": summary,
            "theme": cluster_id,
            "intent": "reflection",
            "source": "external",  # avoids the agent_inference 0.7x penalty
        },
        "context": {
            "summary": summary,
            "topics": terms,
            "embedding": embedding,
        },
        "conversation_history": [
            {"user": guiding_q, "ai": summary},
        ],
    }
    (shard_dir / f"{sid}.json").write_text(json.dumps(shard_data), encoding="utf-8")

    # In-memory index — shape mirrors mcp/test_recall.py:18 _make_index.
    index[sid] = {
        "shard_id": sid,
        "guiding_question": guiding_q,
        "tags": [],
        "meta": {
            "summary": summary,
            "source": "external",
            "theme": cluster_id,
            "intent": "reflection",
            "cluster_id": cluster_id,  # used by _walk_topk_with_cluster_collapse
        },
        "meta_tags": {
            "cluster_id": cluster_id,  # used by the bench for recall ground truth
        },
        "context_summary": summary,
        "context_topics": terms,
        "confidence": 1.0,
        "trust_score": 1.0,
    }

    # Graph entity — spreading activation requires the target to exist here.
    entities[sid] = {"id": sid, "type": "shard"}


# ── Recall@k ──────────────────────────────────────────────────────────────────

def recall_at_k(retrieved_ids: list[str], expected_cluster: str, index: dict, k: int) -> float:
    """
    Fraction of the top-k retrieved shards whose cluster matches the
    expected_cluster. Returns 0.0 if retrieved_ids is empty (k stays in
    the denominator so the score reflects "didn't return enough").
    """
    if not retrieved_ids:
        return 0.0
    hits = 0
    for sid in retrieved_ids[:k]:
        entry = index.get(sid, {})
        cl = entry.get("meta_tags", {}).get("cluster_id")
        if cl == expected_cluster:
            hits += 1
    return hits / k


# ── BenchTimer ────────────────────────────────────────────────────────────────

@dataclass
class BenchTimer:
    log_path: Path
    corpus_size: int
    stage: str
    query_id: str
    in_live_pipeline: bool = True
    extra: dict = field(default_factory=dict)

    def __enter__(self) -> "BenchTimer":
        self._t = time.perf_counter()
        return self

    def __exit__(self, *_exc) -> None:
        duration_ms = (time.perf_counter() - self._t) * 1000
        rec = {
            "ts": datetime.now().isoformat(),
            "corpus_size": self.corpus_size,
            "stage": self.stage,
            "query_id": self.query_id,
            "duration_ms": duration_ms,
            "candidates_in": self.extra.get("candidates_in"),
            "candidates_out": self.extra.get("candidates_out"),
            "recall_at_5": self.extra.get("recall_at_5"),
            "recall_at_10": self.extra.get("recall_at_10"),
            "huginn_called": self.extra.get("huginn_called"),
            "muninn_called": self.extra.get("muninn_called"),
            "muninn_candidates": self.extra.get("muninn_candidates"),
            "in_live_pipeline": self.in_live_pipeline,
        }
        # JSONL append idiom matches mcp/embedding_integrity.py:122-157.
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


# ── Anthropic stub ────────────────────────────────────────────────────────────

_SHARD_ID_RE = re.compile(r'"id"\s*:\s*"([^"]+)"')


def _deterministic_scorer(prompt: str) -> dict[str, float]:
    """
    Parse shard IDs out of the JSON-encoded shards in the prompt and
    return decaying scores (1.0, 0.95, 0.9, ...) in the order they
    appear. Sufficient to make recall non-trivial without semantic
    understanding — the prompt's shard order already reflects the
    pre-filter's ranking, so 'preserve top entries' is a reasonable
    fake-LLM strategy.
    """
    ids = _SHARD_ID_RE.findall(prompt)
    seen: list[str] = []
    for sid in ids:
        if sid not in seen:
            seen.append(sid)
    scores: dict[str, float] = {}
    for i, sid in enumerate(seen):
        scores[sid] = max(0.05, 1.0 - 0.05 * i)
    return scores


class FakeAnthropic:
    """
    Drop-in replacement for `anthropic.Anthropic` used by HUGINN/MUNINN.
    Sleeps a per-model latency to simulate API round-trip, then returns
    the XML-score-tag format ravens._parse_score_xml expects.
    """

    def __init__(self, model_latency: dict[str, float], *_args, **_kwargs):
        self._model_latency = model_latency
        self.messages = self  # `client.messages.create(...)` → self.create

    def create(self, *, model: str, max_tokens: int, messages: list[dict]):
        lat = self._model_latency.get(model)
        if lat is None:
            # Match by substring so config-defined model names ("haiku-4-5",
            # "sonnet-4-6") still route correctly.
            for key, value in self._model_latency.items():
                if key in model:
                    lat = value
                    break
        if lat:
            time.sleep(lat)
        prompt = messages[0]["content"]
        scores = _deterministic_scorer(prompt)
        xml = "\n".join(
            f'<score id="{sid}" value="{v:.2f}">stub</score>'
            for sid, v in scores.items()
        )
        return SimpleNamespace(content=[SimpleNamespace(text=xml)])


def install_anthropic_stub(
    monkeypatch,
    huginn_latency: float = 0.0,
    muninn_latency: float = 0.0,
) -> None:
    """
    Patch ravens.anthropic.Anthropic and CLAUDE_API_KEY so both ravens
    take their LLM branches with deterministic stub responses.

    The stub routes latency by model name substring: "haiku" → huginn,
    "sonnet" → muninn. Matches the real Anthropic model IDs configured
    in mcp/config.py (claude-haiku-4-5-*, claude-sonnet-4-6).
    """
    # CLAUDE_API_KEY is a *cached* module-level constant in config.py
    # (config.py:88 reads os.environ at import time). Setting the env
    # var after config.py is loaded has no effect, so patch the
    # attribute on the config module directly.
    monkeypatch.setenv("CLAUDE_API_KEY", "stub-key")
    monkeypatch.setattr("config.CLAUDE_API_KEY", "stub-key", raising=False)
    model_latency = {"haiku": huginn_latency, "sonnet": muninn_latency}
    monkeypatch.setattr(
        "ravens.anthropic.Anthropic",
        lambda **kw: FakeAnthropic(model_latency=model_latency),
    )


# ── Embedding stub ────────────────────────────────────────────────────────────

def install_embedding_stub(monkeypatch):
    """
    Replace nova_embeddings_local.get_embedding_model_if_ready and
    generate_local_embedding so MUNINN's local rerank takes the cosine
    branch (ravens.py:540) instead of the passthrough-while-loading
    branch (ravens.py:533).

    The fake embedding is a deterministic 384-d unit vector keyed off
    the input text — preserves cosine signal across calls without
    actually loading sentence-transformers.
    """
    sentinel = object()

    def fake_get_model_if_ready():
        return sentinel

    def fake_generate(text: str) -> list[float]:
        # Keyed off text only — no cluster info — so it works for both
        # query strings (which we want to land near their target cluster)
        # and arbitrary shard summaries.
        seed = int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        vec = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    # ravens.py imports these names lazily inside _local_rerank
    # (ravens.py:530), so patching the source module is sufficient.
    monkeypatch.setattr(
        "nova_embeddings_local.get_embedding_model_if_ready", fake_get_model_if_ready
    )
    monkeypatch.setattr(
        "nova_embeddings_local.generate_local_embedding", fake_generate
    )


# ── Arrow cache disable ───────────────────────────────────────────────────────

def disable_arrow_cache(monkeypatch):
    """
    Pin ARROW_AVAILABLE=False so MUNINN._local_rerank takes the
    per-shard JSON loop (ravens.py:572) instead of the process-wide
    Arrow cache (which would contaminate timings across corpus sizes).
    """
    monkeypatch.setattr("arrow_cache.ARROW_AVAILABLE", False, raising=False)
