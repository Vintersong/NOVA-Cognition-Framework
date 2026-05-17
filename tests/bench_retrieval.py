"""
bench_retrieval.py — Stage-level benchmark for NOVA's retrieval pipeline.

Excluded from default `pytest` runs by the `benchmark` marker registered
in pytest.ini. Run explicitly:

    NOVA_BENCH=1 NOVA_BENCH_LOG=bench_retrieval.jsonl \\
        python -m pytest tests/bench_retrieval.py -m benchmark -s

The smoke test (test_bench_smoke) is also marked `benchmark` and runs at
n_shards=20 in <5s; use it to validate the JSONL schema after edits.

Each stage emits one JSONL row per query via BenchTimer. Spreading
activation emits its own row from inside Muninn.rerank when NOVA_BENCH=1
(see mcp/ravens.py post-edit).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from bench_corpus import (
    BenchTimer,
    Corpus,
    build_corpus,
    disable_arrow_cache,
    install_anthropic_stub,
    install_embedding_stub,
    recall_at_k,
)

# Imported from mcp/ via tests/conftest.py sys.path entry.
import facts
import ravens
import recall


# ── Shared session log ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def bench_log_path(tmp_path_factory) -> Path:
    """
    Where stage rows get written. Resolution:
      1. NOVA_BENCH_LOG env var (recommended for real sweeps)
      2. otherwise a per-session tmp file

    Truncated once per session so reruns don't pollute earlier numbers.
    """
    env_path = os.environ.get("NOVA_BENCH_LOG")
    if env_path:
        p = Path(env_path)
        p.parent.mkdir(parents=True, exist_ok=True)
    else:
        p = tmp_path_factory.mktemp("bench") / "bench.jsonl"
    p.write_text("", encoding="utf-8")
    return p


# ── Common monkeypatching ─────────────────────────────────────────────────────

def _patch_environment(monkeypatch, corpus: Corpus, log_path: Path,
                       huginn_latency: float, muninn_latency: float) -> None:
    """Apply all bench-scope patches: Anthropic stub, embedding stub,
    Arrow disable, synthetic graph, NOVA_BENCH inner-timer env."""
    install_anthropic_stub(monkeypatch, huginn_latency, muninn_latency)
    install_embedding_stub(monkeypatch)
    disable_arrow_cache(monkeypatch)
    monkeypatch.setattr("graph.load_graph", lambda: corpus.graph, raising=False)
    monkeypatch.setenv("NOVA_BENCH", "1")
    monkeypatch.setenv("NOVA_BENCH_LOG", str(log_path))
    monkeypatch.setenv("NOVA_BENCH_CORPUS_SIZE", str(len(corpus.index)))
    # Disable any project_context filtering — synth shards omit the field
    # but be defensive.
    monkeypatch.setenv("NOVA_PROJECT_CONTEXT", "")


def _make_ravens(corpus: Corpus, tmp_path: Path) -> tuple[ravens.Huginn, ravens.Muninn]:
    """Construct Huginn + Muninn pointing at the corpus' shard dir.
    usage_log_file lives in tmp_path so production logs aren't touched."""
    usage_log = tmp_path / "usage.jsonl"
    huginn = ravens.Huginn(
        shard_dir=str(corpus.shard_dir),
        usage_log_file=str(usage_log),
        confidence_threshold=0.7,
    )
    muninn = ravens.Muninn(
        shard_dir=str(corpus.shard_dir),
        usage_log_file=str(usage_log),
    )
    return huginn, muninn


# ── Probes ────────────────────────────────────────────────────────────────────

def probe_facts(query: str, expected_cluster: str, corpus: Corpus,
                log_path: Path, query_id: str) -> None:
    """Stage 1: SQLite facts pre-filter.

    `facts.search_facts` returns [] when FACTS_INDEX_FILE is absent (which
    is the case in tmp_path setup). The timing still reflects the existence-
    check cost — matches production behavior on machines with no facts DB.
    Recall fields are null because facts results have no cluster signal.
    """
    with BenchTimer(log_path, len(corpus.index), "facts_prefilter", query_id) as t:
        results = facts.search_facts(query, confidence=1, limit=5)
        t.extra["candidates_in"] = len(corpus.index)
        t.extra["candidates_out"] = len(results)


def probe_huginn_local(query: str, expected_cluster: str, corpus: Corpus,
                       huginn: ravens.Huginn, log_path: Path, query_id: str) -> None:
    """Stage 2: HUGINN local cosine/Jaccard pre-filter (no LLM)."""
    with BenchTimer(log_path, len(corpus.index), "huginn_local", query_id) as t:
        scored = huginn._local_retrieve(query, corpus.index, 15)  # 5*3
        ids = [s[0] for s in scored]
        t.extra["candidates_in"] = len(corpus.index)
        t.extra["candidates_out"] = len(ids)
        t.extra["recall_at_5"] = recall_at_k(ids, expected_cluster, corpus.index, 5)
        t.extra["recall_at_10"] = recall_at_k(ids, expected_cluster, corpus.index, 10)


async def probe_huginn_llm(query: str, expected_cluster: str, corpus: Corpus,
                            huginn: ravens.Huginn, log_path: Path,
                            query_id: str) -> ravens.RetrievalResult:
    """Stage 3: HUGINN end-to-end (local pre-filter + Haiku LLM rescore).

    Returns the RetrievalResult so the MUNINN probes can chain off it.
    """
    candidates_in = len(corpus.index)
    with BenchTimer(log_path, candidates_in, "huginn_llm", query_id) as t:
        result = await huginn.retrieve(query, corpus.index)
        t.extra["candidates_in"] = candidates_in
        t.extra["candidates_out"] = len(result.shard_ids)
        t.extra["huginn_called"] = result.used_llm
        t.extra["recall_at_5"] = recall_at_k(result.shard_ids, expected_cluster, corpus.index, 5)
        t.extra["recall_at_10"] = recall_at_k(result.shard_ids, expected_cluster, corpus.index, 10)
    return result


def probe_muninn_local(query: str, expected_cluster: str, corpus: Corpus,
                       muninn: ravens.Muninn, huginn_result: ravens.RetrievalResult,
                       log_path: Path, query_id: str) -> None:
    """Stage 4: MUNINN local cosine rerank (no LLM)."""
    candidates_in = len(huginn_result.shard_ids)
    with BenchTimer(log_path, len(corpus.index), "muninn_local", query_id) as t:
        rerank = muninn._local_rerank(query, huginn_result, corpus.index, 5)
        ids = rerank["shard_ids"]
        t.extra["candidates_in"] = candidates_in
        t.extra["candidates_out"] = len(ids)
        t.extra["recall_at_5"] = recall_at_k(ids, expected_cluster, corpus.index, 5)
        t.extra["recall_at_10"] = recall_at_k(ids, expected_cluster, corpus.index, 10)


async def probe_muninn_llm(query: str, expected_cluster: str, corpus: Corpus,
                            muninn: ravens.Muninn, huginn_result: ravens.RetrievalResult,
                            log_path: Path, query_id: str) -> ravens.RetrievalResult:
    """Stage 5: MUNINN end-to-end (local rerank + Sonnet LLM + spreading
    activation). Spreading activation emits its own row from inside
    Muninn.rerank — see mcp/ravens.py post-edit."""
    candidates_in = len(huginn_result.shard_ids)
    with BenchTimer(log_path, len(corpus.index), "muninn_llm", query_id) as t:
        result = await muninn.rerank(query, huginn_result, corpus.index)
        t.extra["candidates_in"] = candidates_in
        t.extra["candidates_out"] = len(result.shard_ids)
        t.extra["muninn_called"] = result.used_llm
        t.extra["muninn_candidates"] = candidates_in
        t.extra["recall_at_5"] = recall_at_k(result.shard_ids, expected_cluster, corpus.index, 5)
        t.extra["recall_at_10"] = recall_at_k(result.shard_ids, expected_cluster, corpus.index, 10)
    return result


def probe_cluster_collapse(query: str, expected_cluster: str, corpus: Corpus,
                            muninn_result: ravens.RetrievalResult,
                            log_path: Path, query_id: str) -> None:
    """Stage 7: Legacy cluster collapse walk — NOT in live pipeline.
    Standalone probe fed MUNINN's output as the scored list."""
    scored = [(sid, muninn_result.scores.get(sid, 0.0)) for sid in muninn_result.shard_ids]
    eligible = {sid: corpus.index[sid] for sid in muninn_result.shard_ids if sid in corpus.index}
    with BenchTimer(
        log_path, len(corpus.index), "cluster_collapse_offpath", query_id,
        in_live_pipeline=False,
    ) as t:
        results = recall._walk_topk_with_cluster_collapse(scored, eligible, 10)
        ids = [r["shard_id"] for r in results]
        t.extra["candidates_in"] = len(scored)
        t.extra["candidates_out"] = len(results)
        t.extra["recall_at_5"] = recall_at_k(ids, expected_cluster, corpus.index, 5)
        t.extra["recall_at_10"] = recall_at_k(ids, expected_cluster, corpus.index, 10)


# ── Test runner ──────────────────────────────────────────────────────────────

async def _run_sweep(corpus: Corpus, huginn: ravens.Huginn, muninn: ravens.Muninn,
                     log_path: Path) -> None:
    """Iterate every (query, expected_cluster) and run all probes."""
    for qi, (query, expected_cluster) in enumerate(corpus.queries):
        query_id = f"q{qi:03d}"
        # The MUNINN inner timer reads this from the env to tag its row.
        os.environ["NOVA_BENCH_QUERY_ID"] = query_id
        probe_facts(query, expected_cluster, corpus, log_path, query_id)
        probe_huginn_local(query, expected_cluster, corpus, huginn, log_path, query_id)
        huginn_result = await probe_huginn_llm(query, expected_cluster, corpus,
                                                huginn, log_path, query_id)
        probe_muninn_local(query, expected_cluster, corpus, muninn, huginn_result,
                           log_path, query_id)
        muninn_result = await probe_muninn_llm(query, expected_cluster, corpus,
                                                muninn, huginn_result, log_path, query_id)
        probe_cluster_collapse(query, expected_cluster, corpus, muninn_result,
                               log_path, query_id)


# Latencies for the LLM stubs. Set to 0 in the smoke test so it stays under
# the 5s target. For the real sweep these approximate observed p50 round-trip.
_REAL_HUGINN_LATENCY_S = 0.4
_REAL_MUNINN_LATENCY_S = 1.2


@pytest.mark.benchmark
def test_bench_smoke(monkeypatch, tmp_path, bench_log_path: Path) -> None:
    """Fast 20-shard run. Validates the JSONL schema and that every
    stage emits at least one row. Must finish well under 5s."""
    corpus = build_corpus(tmp_path, n_shards=20, n_queries_per_cluster=2)
    _patch_environment(monkeypatch, corpus, bench_log_path, 0.0, 0.0)
    huginn, muninn = _make_ravens(corpus, tmp_path)
    asyncio.run(_run_sweep(corpus, huginn, muninn, bench_log_path))

    # ── Schema validation ────────────────────────────────────────────────────
    rows = [json.loads(line) for line in bench_log_path.read_text().splitlines() if line.strip()]
    assert rows, "smoke run produced no JSONL rows"

    expected_stages = {
        "facts_prefilter",
        "huginn_local",
        "huginn_llm",
        "muninn_local",
        "muninn_llm",
        "cluster_collapse_offpath",
        "spreading_activation_inner",
    }
    observed_stages = {r["stage"] for r in rows}
    missing = expected_stages - observed_stages
    assert not missing, f"missing stage rows: {missing}"

    required_fields = {"corpus_size", "stage", "duration_ms", "in_live_pipeline"}
    for r in rows:
        assert required_fields.issubset(r.keys()), f"row missing fields: {r}"
        assert isinstance(r["duration_ms"], (int, float))
        assert r["duration_ms"] >= 0.0
        for rk in ("recall_at_5", "recall_at_10"):
            v = r.get(rk)
            if v is not None:
                assert 0.0 <= v <= 1.0, f"{rk} out of range: {v} in {r}"

    # The legacy probe must declare itself off-path; everything else lives in
    # the live pipeline.
    for r in rows:
        if r["stage"] == "cluster_collapse_offpath":
            assert r["in_live_pipeline"] is False
        else:
            assert r["in_live_pipeline"] is True


@pytest.mark.benchmark
@pytest.mark.parametrize("n_shards", [50, 200, 500, 1000])
def test_bench_sweep(monkeypatch, tmp_path, bench_log_path: Path, n_shards: int) -> None:
    """The real sweep. ~20 queries × 6 stages × 4 sizes with stubbed
    LLM latencies — expect ~8 min total wall clock."""
    corpus = build_corpus(tmp_path, n_shards=n_shards)
    _patch_environment(
        monkeypatch, corpus, bench_log_path,
        huginn_latency=_REAL_HUGINN_LATENCY_S,
        muninn_latency=_REAL_MUNINN_LATENCY_S,
    )
    huginn, muninn = _make_ravens(corpus, tmp_path)
    asyncio.run(_run_sweep(corpus, huginn, muninn, bench_log_path))
