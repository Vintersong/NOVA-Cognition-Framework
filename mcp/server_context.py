"""
server_context.py — process-scoped singletons for the NOVA MCP server.

The NOVA tool handlers (currently in nova_server.py, soon split across
shard_tools.py / graph_tools.py / session_tools.py / forgemaster_tools.py)
all reach into the same ~10 singletons: the two ravens, NÓTT, the hook
registry, permission context, capability gate, audit log, session store,
running usage counters, the active skill manifest, and the rotating
server session ID.

ServerContext bundles these into one object so the handler modules can
be unit-testable and so nova_server.py shrinks to bootstrap + wiring.
The bootstrap() classmethod reproduces what nova_server.py used to do
inline; the order matters (Nott depends on store/maintenance functions,
hooks depend on Nott, etc.) — keep it intact when editing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from audit_log import AuditLog
from capability_gate import CapabilityGate
from config import (
    GRAPH_FILE,
    SHARD_DIR,
    SESSION_STORE_DIR,
    SKILL_AUDIT_LOG_FILE,
    USAGE_LOG_FILE,
)
from graph import load_graph, save_graph
from hooks import NovaHookEvent, NovaHookRegistry
from maintenance import (
    apply_confidence_decay,
    find_merge_candidates,
    maybe_compact_shard,
)
from models import UsageSummary
from nott import Nott, NottTrigger
from permissions import ToolPermissionContext, set_active as _set_active_permissions
from ravens import Huginn, Muninn
from session_store import SessionStore
from skill_manifest import SkillManifest
from store import load_index, load_shard, save_shard, update_index

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


def _build_permission_context() -> ToolPermissionContext:
    denied_tools = os.environ.get("NOVA_DENIED_TOOLS", "")
    denied_prefixes = os.environ.get("NOVA_DENIED_PREFIXES", "")
    return ToolPermissionContext.from_iterables(
        deny_tools=[t for t in denied_tools.split(",") if t.strip()],
        deny_prefixes=[p for p in denied_prefixes.split(",") if p.strip()],
    )


def _pre_compact_stub(_data: dict, _shard_id: str) -> None:
    # TODO: replace with lightweight Haiku fact-extraction so key statements
    # survive maybe_compact_shard's lossy summarization. Wired into NÓTT's
    # pre_compact hook; today it's a deliberate no-op.
    return None


def _update_graph_entity_confidence(shard_id: str, data: dict) -> None:
    """Update graph entity confidence — runs in executor to keep event loop free."""
    graph = load_graph()
    if shard_id in graph.get("entities", {}):
        graph["entities"][shard_id]["confidence"] = (
            data.get("meta_tags", {}).get("confidence", 1.0)
        )
        save_graph(graph)


@dataclass
class ServerContext:
    """All process-scoped singletons used by NOVA tool handlers.

    Mutable fields (``active_skill``, ``server_session_id``) are attributes
    rather than module globals so hook callbacks and the eventual extracted
    handler modules mutate the same instance the handlers observe.
    """

    huginn: Huginn
    muninn: Muninn
    nott: Nott
    hooks: NovaHookRegistry
    permission_context: ToolPermissionContext
    capability_gate: CapabilityGate
    skill_audit_log: AuditLog
    session_store: SessionStore
    session_usage: UsageSummary
    active_skill: SkillManifest
    server_session_id: str

    # Per-shard async locks — WeakValueDictionary so unused locks get GC'd
    # once no task holds or waits on them. Foreground + background tasks
    # keep a strong ref while in flight, so overlapping calls share one
    # lock and it's dropped afterwards.
    _shard_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = field(
        default_factory=weakref.WeakValueDictionary
    )

    # NÓTT serialisation — only one cycle at a time.
    _nott_lock: threading.Lock = field(default_factory=threading.Lock)

    def get_shard_lock(self, shard_id: str) -> asyncio.Lock:
        lock = self._shard_locks.get(shard_id)
        if lock is None:
            lock = asyncio.Lock()
            self._shard_locks[shard_id] = lock
        return lock

    def rotate_session_id(self) -> None:
        """Rotate the audit-log session ID at session boundaries."""
        self.server_session_id = uuid.uuid4().hex

    def run_nott_in_thread(self, trigger: NottTrigger) -> None:
        """Run a NÓTT cycle in an isolated thread with its own event loop.

        Only one NÓTT cycle runs at a time — concurrent triggers are
        dropped. Prevents multiple threads hammering the shard directory
        simultaneously (each full update_index() scans 580+ files). The
        next tool call will re-trigger if needed.
        """
        nott = self.nott
        lock = self._nott_lock

        def _run() -> None:
            if not lock.acquire(blocking=False):
                return  # another cycle is already running — skip
            try:
                asyncio.run(nott.run(trigger))
            finally:
                lock.release()

        threading.Thread(target=_run, daemon=True).start()

    @classmethod
    def bootstrap(cls) -> "ServerContext":
        """Construct singletons in dependency order and wire hooks.

        Mirrors what nova_server.py used to do inline at module-load time.
        """
        from config import HUGINN_CONFIDENCE_THRESHOLD

        permission_context = _build_permission_context()
        # Publish so externally-registered tool modules (nidhogg, evolve,
        # gemini, wiki, facts, external_retrieval) see the same policy.
        _set_active_permissions(permission_context)

        skill_audit_log = AuditLog(SKILL_AUDIT_LOG_FILE)
        capability_gate = CapabilityGate(audit_log=skill_audit_log)

        huginn = Huginn(
            shard_dir=SHARD_DIR,
            usage_log_file=USAGE_LOG_FILE,
            confidence_threshold=HUGINN_CONFIDENCE_THRESHOLD,
        )
        muninn = Muninn(
            shard_dir=SHARD_DIR,
            usage_log_file=USAGE_LOG_FILE,
        )

        nott = Nott(
            shard_dir=SHARD_DIR,
            graph_file=GRAPH_FILE,
            usage_log_file=USAGE_LOG_FILE,
            load_index_fn=load_index,
            update_index_fn=update_index,
            load_shard_fn=load_shard,
            save_shard_fn=save_shard,
            decay_fn=apply_confidence_decay,
            compact_fn=maybe_compact_shard,
            merge_fn=find_merge_candidates,
            load_graph_fn=load_graph,
            save_graph_fn=save_graph,
            pre_compact_fn=_pre_compact_stub,
        )

        ctx = cls(
            huginn=huginn,
            muninn=muninn,
            nott=nott,
            hooks=NovaHookRegistry(),
            permission_context=permission_context,
            capability_gate=capability_gate,
            skill_audit_log=skill_audit_log,
            session_store=SessionStore(SESSION_STORE_DIR),
            session_usage=UsageSummary(),
            active_skill=SkillManifest.OPERATOR_DIRECT,
            server_session_id=uuid.uuid4().hex,
        )

        ctx._register_default_hooks()
        return ctx

    def _register_default_hooks(self) -> None:
        """Wire NÓTT triggers and session-id rotation to the hook bus."""

        async def _nott_session_start(**_kw: object) -> None:
            self.run_nott_in_thread(NottTrigger.SESSION_START)

        async def _nott_post_sprint(**_kw: object) -> None:
            self.run_nott_in_thread(NottTrigger.POST_SPRINT)

        async def _nott_count_threshold(**_kw: object) -> None:
            self.run_nott_in_thread(NottTrigger.COUNT_THRESHOLD)

        async def _refresh_session_id(**_kw: object) -> None:
            self.rotate_session_id()

        self.hooks.register(NovaHookEvent.SESSION_START, _nott_session_start)
        self.hooks.register(NovaHookEvent.POST_SPRINT, _nott_post_sprint)
        self.hooks.register(NovaHookEvent.COUNT_THRESHOLD, _nott_count_threshold)
        self.hooks.register(NovaHookEvent.SESSION_START, _refresh_session_id)
