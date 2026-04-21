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
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

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
    graph_entities_synced: int = 0
    total_shards: int = 0
    duration_ms: float = 0.0
    dry_run: bool = False
    silent: bool = False  # True when SESSION_START found nothing to do

    @property
    def summary(self) -> str:
        return (
            f"Decayed {len(self.decayed_shards)} shards, "
            f"compacted {len(self.compacted_shards)}, "
            f"found {len(self.merge_suggestions)} merge candidates."
        )

    def to_dict(self) -> dict:
        """Serialise to the backwards-compatible response shape."""
        return {
            "status": "consolidation_complete",
            "trigger": self.trigger,
            "decayed_shards": self.decayed_shards,
            "compacted_shards": self.compacted_shards,
            "merge_suggestions": self.merge_suggestions[:10],  # cap at 10
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
        """Synchronous decay — runs in a thread to avoid blocking the event loop."""
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

        return compacted

    async def _compact_pass(self, index: dict, dry_run: bool) -> list[str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._compact_pass_sync, index, dry_run
        )

    # ── Merge suggestions pass ───────────────────────────────────────────

    def _merge_pass_sync(self, index: dict) -> list[dict]:
        """Synchronous merge scan — runs in a thread to avoid blocking the event loop."""
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
