"""
nott.py — NÓTT, the Goddess of Night. NOVA's compaction daemon.

NÓTT runs while you're not looking:
  - Confidence decay on stale shards
  - Auto-compaction of bloated shards
  - Merge candidate surfacing for high-similarity pairs
  - Knowledge graph entity sync

NON-BLOCKING by contract. All invocations in nova_server.py use either:
  asyncio.create_task(_nott.run(...))   — fire-and-forget (never delays a user tool)
  await _nott.run(...)                  — only in nova_shard_consolidate (explicit user request)

Trigger levels control how much work NÓTT does per invocation:
  SESSION_START  — lightweight: decay pass only. Runs on every nova_shard_interact.
  COUNT_THRESHOLD — decay + merge suggestions. Fires when shard count exceeds NOTT_COUNT_THRESHOLD.
  POST_SPRINT    — full cycle: decay + compact + merge + graph sync. Fires after nova_shard_update.
  SCHEDULED      — same as POST_SPRINT. For manual / nova_shard_consolidate invocation.

NottReport is JSON-backwards-compatible with the old nova_shard_consolidate response:
  decayed_shards, compacted_shards, merge_suggestions, total_shards, summary

Usage tracking:
  All operations log to nova_usage.jsonl with operator="NÓTT".
"""

from __future__ import annotations

import asyncio
import atexit
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from timeutils import parse_iso, now_utc

# Borrowed from hermes-agent cron/scheduler.py:
# when a SESSION_START cycle has nothing to report, suppress the log write
# rather than writing an empty entry.
SILENT_MARKER = "[SILENT]"


# ═══════════════════════════════════════════════════════════
# TRIGGER LEVELS
# ═══════════════════════════════════════════════════════════

class NottTrigger(Enum):
    SESSION_START = "session_start"       # lightweight: decay only
    COUNT_THRESHOLD = "count_threshold"   # decay + merge suggestions
    POST_SPRINT = "post_sprint"           # full: decay + compact + merge + graph
    SCHEDULED = "scheduled"              # full (manual or time-based)


# ═══════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════

@dataclass
class NottReport:
    """
    Result of a NÓTT maintenance cycle.

    JSON-backwards-compatible with the old nova_shard_consolidate response:
    same top-level keys (decayed_shards, compacted_shards, merge_suggestions,
    total_shards, summary) so existing integrations don't break.
    """
    trigger: str
    decayed_shards: list[dict] = field(default_factory=list)
    compacted_shards: list[str] = field(default_factory=list)
    merge_suggestions: list[dict] = field(default_factory=list)
    quarantine_results: list[dict] = field(default_factory=list)
    decay_on_read_results: list[dict] = field(default_factory=list)
    cluster_results: dict = field(default_factory=dict)
    adversarial_results: dict = field(default_factory=dict)
    valence_arousal_results: list[dict] = field(default_factory=list)
    embedding_integrity_results: dict = field(default_factory=dict)
    graph_entities_synced: int = 0
    total_shards: int = 0
    duration_ms: float = 0.0
    dry_run: bool = False
    silent: bool = False  # True when SESSION_START found nothing to do

    @property
    def summary(self) -> str:
        graduated = sum(1 for r in self.quarantine_results if r["outcome"] == "graduated")
        contradicted = sum(1 for r in self.quarantine_results if r["outcome"] == "contradicted")
        q_note = f", graduated {graduated} / contradicted {contradicted} quarantined" if self.quarantine_results else ""
        dor_note = f", decay-on-read penalised {len(self.decay_on_read_results)}" if self.decay_on_read_results else ""
        cl = self.cluster_results
        cluster_note = f", clustered {cl.get('shards_assigned', 0)} shards into {cl.get('clusters_found', 0)} clusters" if cl.get("recomputed") else ""
        adv = self.adversarial_results
        adv_note = f", adversarial found {adv.get('contradictions_found', 0)} contradictions in {adv.get('shards_reviewed', 0)} shards" if adv and not adv.get("skipped") else ""
        va_note = f", scored valence/arousal for {len(self.valence_arousal_results)} shards" if self.valence_arousal_results else ""
        ei = self.embedding_integrity_results
        ei_note = f", embedding integrity: {ei.get('failures', 0)} failures in {ei.get('scanned', 0)} shards" if ei else ""
        return (
            f"Decayed {len(self.decayed_shards)} shards, "
            f"compacted {len(self.compacted_shards)}, "
            f"found {len(self.merge_suggestions)} merge candidates{q_note}{dor_note}{cluster_note}{adv_note}{va_note}{ei_note}."
        )

    def to_dict(self) -> dict:
        """Serialise to the backwards-compatible response shape."""
        return {
            "status": "consolidation_complete",
            "trigger": self.trigger,
            "decayed_shards": self.decayed_shards,
            "compacted_shards": self.compacted_shards,
            "merge_suggestions": self.merge_suggestions[:10],  # cap at 10
            "quarantine_results": self.quarantine_results,
            "decay_on_read_results": self.decay_on_read_results,
            "cluster_results": self.cluster_results,
            "adversarial_results": self.adversarial_results,
            "valence_arousal_results": self.valence_arousal_results,
            "embedding_integrity_results": self.embedding_integrity_results,
            "graph_entities_synced": self.graph_entities_synced,
            "total_shards": self.total_shards,
            "duration_ms": round(self.duration_ms, 1),
            "dry_run": self.dry_run,
            "summary": self.summary,
        }


# ═══════════════════════════════════════════════════════════
# NÓTT DAEMON
# ═══════════════════════════════════════════════════════════

class Nott:
    """
    Automation daemon. Runs maintenance while Odin sleeps.

    Receives function references at construction to avoid circular imports —
    apply_confidence_decay, maybe_compact_shard, find_merge_candidates,
    load_shard, save_shard, update_index, load_graph, save_graph all
    live in nova_server.py and are injected here.
    """

    def __init__(
        self,
        shard_dir: str,
        graph_file: str,
        usage_log_file: str,
        load_index_fn: Callable[[], dict],
        update_index_fn: Callable[[], dict],
        load_shard_fn: Callable[[str], tuple[dict, str]],
        save_shard_fn: Callable[[str, dict], None],
        decay_fn: Callable[[dict], float],
        compact_fn: Callable[[dict, str], bool],
        merge_fn: Callable[[str, dict, dict], list[dict]],
        load_graph_fn: Callable[[], dict],
        save_graph_fn: Callable[[dict], None],
        pre_compact_fn: Optional[Callable[[dict, str], None]] = None,
    ):
        self.shard_dir = shard_dir
        self.graph_file = graph_file
        self.usage_log_file = usage_log_file

        self._load_index = load_index_fn
        self._update_index = update_index_fn
        self._load_shard = load_shard_fn
        self._save_shard = save_shard_fn
        self._decay = decay_fn
        self._compact = compact_fn
        self._find_merge_candidates = merge_fn
        self._load_graph = load_graph_fn
        self._save_graph = save_graph_fn
        self._pre_compact = pre_compact_fn  # optional: extract facts before compacting

        # Dedicated thread pool so NÓTT's long passes never contend with
        # request-path work (ravens retrieval, background enrichment, etc.
        # all share the default asyncio executor).
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="nott"
        )
        # On `evolve.restart_requested` reload the server creates a new Nott
        # without disposing the old one. atexit + close() ensures the threads
        # don't pile up across reloads.
        atexit.register(self.close)

    def close(self) -> None:
        """Shut down the thread pool. Idempotent."""
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    async def run(
        self,
        trigger: NottTrigger,
        dry_run: bool = False,
    ) -> NottReport:
        """
        Run a maintenance cycle at the given trigger level.

        SESSION_START  → _decay_pass only
        COUNT_THRESHOLD → _decay_pass + _merge_pass
        POST_SPRINT / SCHEDULED → all three passes + _graph_sync
        """
        t_start = time.monotonic()
        report = NottReport(trigger=trigger.value, dry_run=dry_run)
        index = self._load_index()

        if trigger in (NottTrigger.POST_SPRINT, NottTrigger.SCHEDULED,
                       NottTrigger.SESSION_START, NottTrigger.COUNT_THRESHOLD):
            report.decayed_shards = await self._decay_pass(index, dry_run)

        if trigger in (NottTrigger.POST_SPRINT, NottTrigger.SCHEDULED,
                       NottTrigger.COUNT_THRESHOLD):
            report.merge_suggestions = await self._merge_pass(index)

        if trigger in (NottTrigger.POST_SPRINT, NottTrigger.SCHEDULED):
            report.compacted_shards = await self._compact_pass(index, dry_run)
            report.graph_entities_synced = self._graph_sync(index, dry_run)

        # Quarantine graduation runs on POST_SPRINT and SESSION_START as well as
        # SCHEDULED — otherwise hook-extracted shards stay penalised forever
        # because users rarely invoke nova_shard_consolidate manually.
        # The pass early-returns cheaply when no shards have quarantine_until.
        if trigger in (NottTrigger.POST_SPRINT, NottTrigger.SCHEDULED,
                       NottTrigger.SESSION_START, NottTrigger.COUNT_THRESHOLD):
            report.quarantine_results = await self._quarantine_pass(index, dry_run)

        if trigger == NottTrigger.SCHEDULED:
            report.decay_on_read_results = await self._decay_on_read_pass(index, dry_run)
            report.cluster_results = await self._cluster_pass(index, dry_run)
            report.adversarial_results = await self._adversarial_pass(index, dry_run)
            report.valence_arousal_results = await self._valence_arousal_pass(index, dry_run)
            report.embedding_integrity_results = await self._embedding_integrity_scan_pass(index, dry_run)

        # Rebuild index after mutations (skip on dry_run)
        if not dry_run and trigger in (NottTrigger.POST_SPRINT, NottTrigger.SCHEDULED):
            index = self._update_index()

        report.total_shards = len(index)
        report.duration_ms = (time.monotonic() - t_start) * 1000.0

        # Mark silent if SESSION_START found nothing to decay
        if trigger == NottTrigger.SESSION_START and not report.decayed_shards:
            report.silent = True

        self._log(report)
        return report

    # ── Decay pass ───────────────────────────────────────────────────────

    def _decay_pass_sync(self, index: dict, dry_run: bool) -> list[dict]:
        """Synchronous decay — runs in a thread to avoid blocking the event loop.

        Fast path: vectorise confidence/last_used over the Arrow cache to pick
        decay candidates, then re-read each via load_shard and mutate only
        ``meta_tags.confidence`` so every other on-disk field round-trips
        untouched. Falls back to the per-shard loop when pyarrow is absent.
        """
        try:
            from arrow_cache import ARROW_AVAILABLE, get_arrow_cache
        except Exception:
            ARROW_AVAILABLE = False
            get_arrow_cache = None  # type: ignore[assignment]

        if ARROW_AVAILABLE:
            try:
                from config import DECAY_RATE, DECAY_INTERVAL_DAYS, MEMORY_KIND_DECAY_RATES
                cache = get_arrow_cache()
                candidates = cache.decay_candidates(
                    now=now_utc(),
                    decay_rate=DECAY_RATE,
                    interval_days=DECAY_INTERVAL_DAYS,
                    kind_rates=MEMORY_KIND_DECAY_RATES,
                )
                decayed: list[dict] = []
                for shard_id, old_conf, new_conf in candidates:
                    try:
                        data, filepath = self._load_shard(shard_id)
                    except FileNotFoundError:
                        continue
                    data.setdefault("meta_tags", {})["confidence"] = round(new_conf, 4)
                    decayed.append({
                        "shard_id": shard_id,
                        "old_confidence": round(old_conf, 4),
                        "new_confidence": round(new_conf, 4),
                    })
                    if not dry_run:
                        self._save_shard(filepath, data)
                return decayed
            except Exception:
                # Any failure in the Arrow fast path falls through to the
                # legacy loop below — never let a maintenance cycle abort.
                pass

        decayed = []
        for shard_id, entry in list(index.items()):
            tags = entry.get("tags", [])
            if "forgotten" in tags:
                continue
            try:
                data, filepath = self._load_shard(shard_id)
            except FileNotFoundError:
                continue

            old_confidence = data.get("meta_tags", {}).get("confidence", 1.0)
            new_confidence = self._decay(data)

            if new_confidence < old_confidence:
                decayed.append({
                    "shard_id": shard_id,
                    "old_confidence": round(old_confidence, 4),
                    "new_confidence": round(new_confidence, 4),
                })
                if not dry_run:
                    self._save_shard(filepath, data)

        return decayed

    async def _decay_pass(self, index: dict, dry_run: bool) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._decay_pass_sync, index, dry_run
        )

    # ── Compact pass ─────────────────────────────────────────────────────

    def _compact_pass_sync(self, index: dict, dry_run: bool) -> list[str]:
        """Synchronous compaction — runs in a thread to avoid blocking the event loop."""
        compacted = []
        for shard_id in list(index.keys()):
            tags = index[shard_id].get("tags", [])
            if "forgotten" in tags:
                continue
            try:
                data, filepath = self._load_shard(shard_id)
            except FileNotFoundError:
                continue

            # Pre-compact hook: extract key facts before turns are summarised away
            if self._pre_compact is not None:
                try:
                    self._pre_compact(data, shard_id)
                except Exception:
                    pass  # never let a hook abort compaction

            was_compacted = self._compact(data, shard_id)
            if was_compacted:
                compacted.append(shard_id)
                if not dry_run:
                    self._save_shard(filepath, data)
                    # Lazy migration: convert to YAML+MD format after compaction.
                    # Only converts .json shards — already-.md shards are a no-op.
                    if filepath.endswith(".json"):
                        try:
                            from shard_format import convert_shard_file
                            convert_shard_file(filepath, delete_json=True)
                        except Exception:
                            pass  # never abort compaction on migration failure

        return compacted

    async def _compact_pass(self, index: dict, dry_run: bool) -> list[str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._compact_pass_sync, index, dry_run
        )

    # ── Merge suggestions pass ───────────────────────────────────────────

    def _merge_pass_sync(self, index: dict) -> list[dict]:
        """Synchronous merge scan — runs in a thread to avoid blocking the event loop.

        Fast path: one ``(k, 384) @ (384, k)`` matmul over the cached embedding
        matrix instead of the legacy O(N²) per-shard JSON read loop. Returns
        the same dict shape (shard_a, shard_b, similarity, question_a,
        question_b) the cluster/merge UI already consumes.
        """
        try:
            from arrow_cache import ARROW_AVAILABLE, get_arrow_cache
        except Exception:
            ARROW_AVAILABLE = False
            get_arrow_cache = None  # type: ignore[assignment]

        if ARROW_AVAILABLE:
            try:
                from config import MERGE_SIMILARITY_THRESHOLD
                from graph import add_corroborated_by
                from maintenance import apply_confidence_corroboration
                enriched_ids = {
                    sid for sid, entry in index.items()
                    if "enriched" in entry.get("tags", [])
                }
                if enriched_ids:
                    pairs = get_arrow_cache().merge_candidates(
                        enriched_ids, MERGE_SIMILARITY_THRESHOLD,
                    )
                    results = []
                    for a, b, sim, qa, qb in pairs:
                        results.append({
                            "shard_a": a,
                            "shard_b": b,
                            "similarity": sim,
                            "question_a": qa,
                            "question_b": qb,
                        })
                        # Exact duplicates (sim=1.0): auto-write corroborated_by
                        # and boost confidence on both shards. These are the same
                        # content written twice — strongest possible corroboration.
                        if sim >= 1.0:
                            try:
                                add_corroborated_by(a, b)
                                add_corroborated_by(b, a)
                                for sid in (a, b):
                                    data, fp = self._load_shard(sid)
                                    apply_confidence_corroboration(data)
                                    self._save_shard(fp, data)
                            except Exception:
                                pass
                    return results
            except Exception:
                pass

        suggestions = []
        checked: set[tuple[str, str]] = set()

        for shard_id, entry in index.items():
            if "enriched" not in entry.get("tags", []):
                continue
            if shard_id in {p for pair in checked for p in pair}:
                continue
            try:
                data, _ = self._load_shard(shard_id)
                candidates = self._find_merge_candidates(shard_id, data, index)
                for c in candidates:
                    pair = tuple(sorted([shard_id, c["shard_id"]]))
                    if pair not in checked:
                        suggestions.append({
                            "shard_a": shard_id,
                            "shard_b": c["shard_id"],
                            "similarity": c["similarity"],
                            "question_a": data.get("guiding_question", ""),
                            "question_b": c.get("guiding_question", ""),
                        })
                        checked.add(pair)
            except FileNotFoundError:
                continue

        return suggestions

    async def _merge_pass(self, index: dict) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._merge_pass_sync, index
        )

    # ── Quarantine graduation pass ───────────────────────────────────────

    def _quarantine_pass_sync(self, index: dict, dry_run: bool) -> list[dict]:
        """
        Inspect every shard whose quarantine_until is in the past.
        - If a contradicts edge points at it: apply QUARANTINE_PENALTY to confidence, clear field.
        - Otherwise: graduate (clear quarantine_until).
        Runs on POST_SPRINT, SESSION_START, SCHEDULED, and COUNT_THRESHOLD triggers.
        Early-exits when no shards are quarantined to keep SESSION_START cheap.
        """
        # Fast path: nothing to do if no shard has quarantine_until set.
        if not any(
            entry.get("meta", {}).get("quarantine_until")
            for entry in index.values()
        ):
            return []

        now = now_utc()
        graph = self._load_graph()
        relations = graph.get("relations", [])
        contradicted_ids = {r["target"] for r in relations if r["type"] == "contradicts"}

        results = []
        for shard_id, entry in list(index.items()):
            quarantine_until_str = entry.get("meta", {}).get("quarantine_until")
            if not quarantine_until_str:
                continue
            quarantine_until = parse_iso(quarantine_until_str)
            if quarantine_until is None:
                continue
            if quarantine_until > now:
                continue  # still in window

            try:
                data, filepath = self._load_shard(shard_id)
            except FileNotFoundError:
                continue

            shard_meta = data.setdefault("meta_tags", {})
            shard_meta["quarantine_until"] = None

            if shard_id in contradicted_ids:
                from config import QUARANTINE_PENALTY
                old_conf = shard_meta.get("confidence", 1.0)
                shard_meta["confidence"] = round(max(0.1, old_conf * QUARANTINE_PENALTY), 4)
                outcome = "contradicted"
            else:
                outcome = "graduated"

            if not dry_run:
                self._save_shard(filepath, data)

            results.append({"shard_id": shard_id, "outcome": outcome})

        return results

    async def _quarantine_pass(self, index: dict, dry_run: bool) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._quarantine_pass_sync, index, dry_run
        )

    # ── Decay-on-read pass ───────────────────────────────────────────────────

    def _decay_on_read_pass_sync(self, index: dict, dry_run: bool) -> list[dict]:
        """
        Penalise shards retrieved too frequently without corroboration.

        For each shard accessed more than DECAY_ON_READ_THRESHOLD times in
        DECAY_ON_READ_WINDOW_DAYS: apply DECAY_ON_READ_PENALTY if no
        corroborated_by edge was added to it within the same window.
        Runs only on SCHEDULED trigger.
        """
        from config import DECAY_ON_READ_THRESHOLD, DECAY_ON_READ_WINDOW_DAYS, DECAY_ON_READ_PENALTY
        from access_log import read_access_log
        from datetime import timedelta

        access_map = read_access_log(DECAY_ON_READ_WINDOW_DAYS)
        if not access_map:
            return []

        graph = self._load_graph()
        relations = graph.get("relations", [])
        window_start = now_utc() - timedelta(days=DECAY_ON_READ_WINDOW_DAYS)

        corroborated_recently: set[str] = set()
        for r in relations:
            if r.get("type") != "corroborated_by":
                continue
            created = parse_iso(r.get("created_at"))
            if created is not None and created >= window_start:
                corroborated_recently.add(r["source"])

        results = []
        for shard_id, timestamps in access_map.items():
            if len(timestamps) <= DECAY_ON_READ_THRESHOLD:
                continue
            if shard_id in corroborated_recently:
                continue
            if shard_id not in index:
                continue

            try:
                data, filepath = self._load_shard(shard_id)
            except FileNotFoundError:
                continue

            shard_meta = data.setdefault("meta_tags", {})
            old_conf = shard_meta.get("confidence", 1.0)
            new_conf = round(max(0.1, old_conf - DECAY_ON_READ_PENALTY), 4)
            shard_meta["confidence"] = new_conf

            if not dry_run:
                self._save_shard(filepath, data)

            results.append({
                "shard_id": shard_id,
                "access_count": len(timestamps),
                "old_confidence": round(old_conf, 4),
                "new_confidence": new_conf,
            })

        return results

    async def _decay_on_read_pass(self, index: dict, dry_run: bool) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._decay_on_read_pass_sync, index, dry_run
        )

    # ── Cluster pass ─────────────────────────────────────────────────────────

    def _cluster_pass_sync(self, index: dict, dry_run: bool) -> dict:
        """
        Run community detection on the knowledge graph and write cluster_id to
        every shard's meta_tags. Only runs when topology has changed enough
        (NOVA_CLUSTER_RECOMPUTE_THRESHOLD, default 5% edge-count delta).

        Returns a summary dict: {recomputed, clusters_found, shards_assigned, algorithm}.
        """
        from clustering import detect_communities, should_recompute, stamp_cluster_run

        graph = self._load_graph()
        if not should_recompute(graph):
            return {"recomputed": False, "clusters_found": 0, "shards_assigned": 0}

        membership = detect_communities(graph)
        if not membership:
            return {"recomputed": True, "clusters_found": 0, "shards_assigned": 0, "algorithm": "none"}

        clusters_found = len(set(membership.values()))
        assigned = 0

        for shard_id, cluster_id in membership.items():
            if shard_id not in index:
                continue
            try:
                data, filepath = self._load_shard(shard_id)
            except FileNotFoundError:
                continue

            data.setdefault("meta_tags", {})["cluster_id"] = cluster_id
            if not dry_run:
                self._save_shard(filepath, data)
            assigned += 1

        if not dry_run:
            edge_count = len(graph.get("relations", []))
            stamp_cluster_run(graph, edge_count)
            self._save_graph(graph)

        return {
            "recomputed": True,
            "clusters_found": clusters_found,
            "shards_assigned": assigned,
        }

    async def _cluster_pass(self, index: dict, dry_run: bool) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._cluster_pass_sync, index, dry_run
        )

    # ── Adversarial pass ─────────────────────────────────────────────────────

    def _adversarial_pass_sync(self, index: dict, dry_run: bool) -> dict:
        """
        Send top-N highest-confidence shards to Gemini Flash for contradiction
        hunting. Runs at most once per ADVERSARIAL_MIN_INTERVAL_DAYS.
        Findings become `contradicts` edges with adversarial-pass provenance.
        """
        from adversarial import run_adversarial_pass
        from graph import add_relation

        graph = self._load_graph()
        return run_adversarial_pass(
            index=index,
            graph=graph,
            save_graph_fn=self._save_graph,
            add_relation_fn=add_relation,
            dry_run=dry_run,
        )

    async def _adversarial_pass(self, index: dict, dry_run: bool) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._adversarial_pass_sync, index, dry_run
        )

    # ── Embedding integrity scan pass ────────────────────────────────────

    def _embedding_integrity_scan_pass_sync(self, index: dict, dry_run: bool) -> dict:
        """
        Sweep all enriched shards and re-verify their embedding signatures.

        Catches shards that were mutated on disk after the last Arrow cache build
        (the cache build only covers shards present at build time). Failures are:

        1. Logged to embedding_integrity.jsonl with full diagnostic context.
        2. Written as a ``contradicts`` graph edge — shard_id → synthetic node
           ``integrity_sentinel:{shard_id}`` — so the adversarial edge set reflects
           known tamper events with provenance.

        Returns a summary dict: {scanned, failures, failure_details, skipped}.
        """
        try:
            from embedding_integrity import verify_embedding, sign_embedding, log_integrity_failure
        except ImportError:
            return {"scanned": 0, "failures": 0, "failure_details": [], "skipped": "module_unavailable"}

        try:
            from graph import add_relation
        except ImportError:
            add_relation = None

        scanned = 0
        failures = 0
        failure_details: list[dict] = []

        for shard_id, entry in list(index.items()):
            tags = entry.get("tags", [])
            if "forgotten" in tags or "archived" in tags:
                continue
            enrichment = entry.get("enrichment_status") or entry.get("meta", {}).get("enrichment_status", "")
            if "enriched" not in enrichment:
                continue

            try:
                data, filepath = self._load_shard(shard_id)
            except FileNotFoundError:
                continue

            context = data.get("context", {})
            embedding = context.get("embedding")
            sig = context.get("embedding_sig")

            # Only verify shards that have both an embedding and a stored signature.
            if not isinstance(embedding, list) or sig is None:
                continue

            scanned += 1
            verified = verify_embedding(embedding, sig)
            if verified:
                continue

            failures += 1
            computed = sign_embedding(embedding)
            log_integrity_failure(shard_id, sig, computed, "nott_scan")

            detail: dict = {
                "shard_id": shard_id,
                "stored_sig": sig[:16] + "…" if sig else None,
                "computed_sig": computed[:16] + "…" if computed else None,
            }
            failure_details.append(detail)

            if not dry_run and add_relation is not None:
                sentinel_id = f"integrity_sentinel:{shard_id}"
                try:
                    add_relation(
                        shard_id,
                        sentinel_id,
                        "contradicts",
                        notes="embedding_integrity_failure",
                        reason=f"HMAC-SHA256 mismatch detected by NÓTT scan (stored={sig[:8]}…)",
                    )
                except Exception as exc:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "nott: embedding integrity graph write failed for %s: %s", shard_id, exc
                    )

        return {
            "scanned": scanned,
            "failures": failures,
            "failure_details": failure_details,
        }

    async def _embedding_integrity_scan_pass(self, index: dict, dry_run: bool) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._embedding_integrity_scan_pass_sync, index, dry_run
        )

    # ── Valence/Arousal pass ─────────────────────────────────────────────

    def _valence_arousal_pass_sync(self, index: dict, dry_run: bool) -> list[dict]:
        """Score affective dimensions (valence + arousal) for unscored shards via Haiku.

        Skips shards that already have meta_tags.valence set (not None).
        Batches 15 shards per Haiku call to minimise API cost.
        Writes valence + arousal back to shard JSON and SQLite.

        Valence: 0=very negative/painful, 5=neutral, 9=very positive/joyful
        Arousal: 0=dormant/reflective/calm, 5=moderate, 9=urgent/exciting/high-activation
        """
        from config import CLAUDE_API_KEY

        if not CLAUDE_API_KEY:
            return []

        # Collect unscored shards (valence not set in meta_tags)
        unscored = []
        for shard_id, entry in index.items():
            if "forgotten" in entry.get("tags", []) or "archived" in entry.get("tags", []):
                continue
            meta = entry.get("meta", {})
            if meta.get("valence") is None:
                unscored.append((shard_id, entry))

        if not unscored:
            return []

        import re
        import httpx
        import anthropic
        from nova_shard_db import get_nova_shard_db, encode_state, _epistemic_from_confidence

        BATCH = 15
        results = []
        client = anthropic.Anthropic(
            api_key=CLAUDE_API_KEY,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

        for i in range(0, len(unscored), BATCH):
            batch = unscored[i:i + BATCH]
            summaries = [
                {
                    "id": sid,
                    "question": entry.get("guiding_question", "")[:120],
                    "summary": entry.get("context_summary", "")[:200],
                    "theme": entry.get("meta", {}).get("theme", ""),
                    "confidence": round(entry.get("confidence", 1.0), 2),
                }
                for sid, entry in batch
            ]
            prompt = (
                "Score valence and arousal for each memory shard.\n\n"
                "Valence (0–9): emotional tone of the content itself.\n"
                "  0=very negative/painful/grief, 5=neutral/factual, 9=very positive/joyful/exciting\n\n"
                "Arousal (0–9): activation level / urgency of the topic.\n"
                "  0=dormant/calm/archival, 5=moderate engagement, 9=urgent/high-activation/crisis\n\n"
                "Return ONLY XML tags, one per shard:\n"
                '<va id="<shard_id>" v="<0-9>" a="<0-9>">one-line reason</va>\n\n'
                f"Shards:\n{json.dumps(summaries, indent=2)}"
            )

            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=512,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = response.content[0].text.strip()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "NÓTT valence_arousal_pass batch %d failed: %s", i // BATCH, exc
                )
                continue

            # Parse <va id="..." v="N" a="N">...</va>
            for match in re.finditer(
                r'<va\s+id=["\']([^"\']+)["\']\s+v=["\'](\d)["\']'
                r'\s+a=["\'](\d)["\'][^>]*>([^<]*)</va>',
                raw,
            ):
                sid, v_str, a_str, reason = match.groups()
                valence = max(0, min(9, int(v_str)))
                arousal = max(0, min(9, int(a_str)))

                try:
                    data, filepath = self._load_shard(sid)
                except FileNotFoundError:
                    continue

                meta = data.setdefault("meta_tags", {})
                meta["valence"] = valence
                meta["arousal"] = arousal

                if not dry_run:
                    self._save_shard(filepath, data)
                    # Sync updated state vector to SQLite
                    try:
                        confidence = float(meta.get("confidence", 1.0))
                        epistemic = _epistemic_from_confidence(confidence)
                        db = get_nova_shard_db()
                        db.conn.execute(
                            "UPDATE shards SET valence=?, arousal=?, state=? WHERE id=?",
                            (
                                valence,
                                arousal,
                                encode_state(confidence, valence, arousal, epistemic),
                                sid,
                            ),
                        )
                        db.conn.commit()
                    except Exception:
                        pass

                results.append({
                    "shard_id": sid,
                    "valence": valence,
                    "arousal": arousal,
                    "reason": reason.strip(),
                })

        return results

    async def _valence_arousal_pass(self, index: dict, dry_run: bool) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._valence_arousal_pass_sync, index, dry_run
        )

    # ── Graph sync ───────────────────────────────────────────────────────

    def _graph_sync(self, index: dict, dry_run: bool) -> int:
        """Update confidence values of graph entities to match current shard state."""
        graph = self._load_graph()
        entities = graph.get("entities", {})
        synced = 0

        for shard_id, entry in index.items():
            if shard_id in entities:
                current_conf = entry.get("confidence", 1.0)
                if entities[shard_id].get("confidence") != current_conf:
                    entities[shard_id]["confidence"] = current_conf
                    synced += 1

        if synced > 0 and not dry_run:
            graph["entities"] = entities
            self._save_graph(graph)

        return synced

    # ── Usage logging ────────────────────────────────────────────────────

    def _log(self, report: NottReport):
        # Suppress empty SESSION_START cycles — SILENT_MARKER pattern
        # borrowed from hermes-agent cron/scheduler.py
        if report.silent:
            return
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": "maintenance_cycle",
            "operator": "NÓTT",
            "shards": [],
            "metadata": {
                "trigger": report.trigger,
                "decayed": len(report.decayed_shards),
                "compacted": len(report.compacted_shards),
                "merge_suggestions": len(report.merge_suggestions),
                "graph_entities_synced": report.graph_entities_synced,
                "total_shards": report.total_shards,
                "duration_ms": round(report.duration_ms, 1),
                "dry_run": report.dry_run,
            },
        }
        try:
            with open(self.usage_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
