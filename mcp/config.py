"""
config.py — Centralised environment variable configuration for NOVA.

All modules import from here.  Changing a default means changing one line.
No object construction here — this is pure env-var reading.
"""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
# Data root: defaults to CWD so the server is project-agnostic when registered
# globally. Override with NOVA_DATA_ROOT to pin to a specific directory, or set
# individual NOVA_* path vars for per-path control.
_DATA_ROOT = Path(os.environ.get("NOVA_DATA_ROOT", str(Path.cwd())))


def parse_bool_env(key: str, default: bool = False) -> bool:
    """Parse a boolean env var using common truthy values."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# ── Paths ────────────────────────────────────────────────────────────────────
SHARD_DIR      = os.environ.get("NOVA_SHARD_DIR",     str(_DATA_ROOT /"shards"))
INDEX_FILE     = os.environ.get("NOVA_INDEX_FILE",    str(_DATA_ROOT /"shard_index.json"))
GRAPH_FILE     = os.environ.get("NOVA_GRAPH_FILE",    str(_DATA_ROOT /"shard_graph.json"))
SUMMARY_INDEX_FILE = os.environ.get("NOVA_SUMMARY_INDEX_FILE", str(_DATA_ROOT /"summary_index.json"))
SUMMARY_MARKDOWN_FILE = os.environ.get("NOVA_SUMMARY_MARKDOWN_FILE", str(_DATA_ROOT /"summary_index.md"))
USAGE_LOG_FILE = os.environ.get("NOVA_USAGE_LOG",     str(_DATA_ROOT /"nova_usage.jsonl"))
SESSION_STORE_DIR = os.environ.get(
    "NOVA_SESSION_STORE_DIR", str(_DATA_ROOT /"nova_sessions")
)

# ── Context window / fragment limits ─────────────────────────────────────────
MAX_FRAGMENTS = int(os.environ.get("NOVA_MAX_FRAGMENTS", "10"))

# ── Maintenance policy ────────────────────────────────────────────────────────
COMPACT_THRESHOLD   = int(os.environ.get("NOVA_COMPACT_THRESHOLD", "30"))
COMPACT_KEEP_RECENT = int(os.environ.get("NOVA_COMPACT_KEEP",      "15"))
DECAY_RATE          = float(os.environ.get("NOVA_DECAY_RATE",      "0.05"))
DECAY_INTERVAL_DAYS = int(os.environ.get("NOVA_DECAY_DAYS",        "7"))
MERGE_SIMILARITY_THRESHOLD = float(os.environ.get("NOVA_MERGE_THRESHOLD", "0.85"))

# ── Per-intent decay rates ────────────────────────────────────────────────────
# Halflives: halflife_days = DECAY_INTERVAL_DAYS * ln(2) / rate
# "reflection" matches the legacy uniform rate exactly — existing shards unchanged.
# Override the whole dict via NOVA_KIND_DECAY_RATES (JSON string).
import json as _json
_default_kind_rates: dict[str, float] = {
    "session":      0.10,   # halflife ~49d  — transient session notes
    "event":        0.07,   # halflife ~69d
    "reflection":   0.05,   # halflife ~97d  — current default
    "research":     0.03,   # halflife ~162d
    "project":      0.02,   # halflife ~243d
    "decision":     0.015,  # halflife ~324d — architectural decisions persist
    "architecture": 0.015,  # halflife ~324d
}
MEMORY_KIND_DECAY_RATES: dict[str, float] = _json.loads(
    os.environ.get("NOVA_KIND_DECAY_RATES", _json.dumps(_default_kind_rates))
)

# ── Confidence bands ─────────────────────────────────────────────────────────
CONFIDENCE_LOW_THRESHOLD = float(os.environ.get("NOVA_CONFIDENCE_LOW", "0.4"))

# ── Retrieval source weighting ────────────────────────────────────────────────
# Multiplier applied to retrieval scores for agent_inference shards.
# Values < 1.0 deprioritise agent-generated content relative to external sources.
NOVA_AGENT_INFERENCE_WEIGHT = float(os.environ.get("NOVA_AGENT_INFERENCE_WEIGHT", "0.7"))

# ── Adversarial NÓTT pass ────────────────────────────────────────────────────
# Number of highest-confidence shards checked per adversarial run.
ADVERSARIAL_TOP_N           = int(os.environ.get("NOVA_ADVERSARIAL_TOP_N",            "10"))
# Minimum days between adversarial runs (weekly by default).
ADVERSARIAL_MIN_INTERVAL_DAYS = int(os.environ.get("NOVA_ADVERSARIAL_INTERVAL_DAYS",  "7"))

# ── State-aware retrieval gating ─────────────────────────────────────────────
# When set, shards whose project_context doesn't include this value are excluded.
NOVA_PROJECT_CONTEXT = os.environ.get("NOVA_PROJECT_CONTEXT", "")

# ── Decay on read ─────────────────────────────────────────────────────────────
# Shards retrieved more than THRESHOLD times in WINDOW_DAYS without a new
# corroborated_by edge receive a confidence penalty of PENALTY per NÓTT pass.
ACCESS_LOG_FILE          = os.environ.get("NOVA_ACCESS_LOG",              str(_DATA_ROOT /"shard_access.jsonl"))
DECAY_ON_READ_THRESHOLD  = int(os.environ.get("NOVA_DECAY_ON_READ_THRESHOLD",   "5"))
DECAY_ON_READ_WINDOW_DAYS = int(os.environ.get("NOVA_DECAY_ON_READ_WINDOW_DAYS", "7"))
DECAY_ON_READ_PENALTY    = float(os.environ.get("NOVA_DECAY_ON_READ_PENALTY",   "0.05"))

# ── Quarantine ────────────────────────────────────────────────────────────────
# session_extracted shards are held in quarantine for this many hours.
# During quarantine their retrieval score is multiplied by QUARANTINE_PENALTY.
QUARANTINE_HOURS   = int(os.environ.get("NOVA_QUARANTINE_HOURS",   "48"))
QUARANTINE_PENALTY = float(os.environ.get("NOVA_QUARANTINE_PENALTY", "0.5"))

# ── Shard age classification ──────────────────────────────────────────────────
RECENT_ACCESS_DAYS = int(os.environ.get("NOVA_RECENT_DAYS", "3"))
STALE_ACCESS_DAYS  = int(os.environ.get("NOVA_STALE_DAYS",  "14"))

# ── Norse Pantheon ─────────────────────────────────────────────────────────────
HUGINN_CONFIDENCE_THRESHOLD = float(os.environ.get("HUGINN_CONFIDENCE_THRESHOLD", "0.7"))
NOTT_COUNT_THRESHOLD        = int(os.environ.get("NOTT_COUNT_THRESHOLD",         "100"))

# ── Spreading activation (MUNINN third pass) ──────────────────────────────────
# Minimum graph edge count required to attempt spreading activation in MUNINN.
# Below this threshold the graph is too sparse for meaningful propagation.
NOVA_ACTIVATION_MIN_EDGES = int(os.environ.get("NOVA_ACTIVATION_MIN_EDGES", "10"))

# ── Ravens LLM config ────────────────────────────────────────────────────────
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
HUGINN_MODEL      = os.environ.get("HUGINN_MODEL",      "claude-haiku-4-5-20251001")
MUNINN_MODEL      = os.environ.get("MUNINN_MODEL",      "claude-sonnet-4-6")
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL",      "gemini-2.5-flash")

# Forgemaster per-role model overrides. Default to MUNINN_MODEL for the
# Anthropic roles and GEMINI_MODEL for the implementer so behavior is unchanged
# when these are unset. Point a single role at a different model (e.g. an Opus
# alias for the orchestrator on high-stakes sprints) without rebuilding.
FORGEMASTER_ORCHESTRATOR_MODEL = os.environ.get("FORGEMASTER_ORCHESTRATOR_MODEL", MUNINN_MODEL)
FORGEMASTER_PLANNER_MODEL      = os.environ.get("FORGEMASTER_PLANNER_MODEL",      MUNINN_MODEL)
FORGEMASTER_REVIEWER_MODEL     = os.environ.get("FORGEMASTER_REVIEWER_MODEL",     MUNINN_MODEL)
FORGEMASTER_IMPLEMENTER_MODEL  = os.environ.get("FORGEMASTER_IMPLEMENTER_MODEL",  GEMINI_MODEL)

# ── Input validation patterns ──────────────────────────────────────────────────
# Session IDs are persisted as filenames: keep strict and portable.
# 1-128 chars total, start alnum, then alnum / dot / underscore / dash.
SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"

# ── Facts corpus (shard_parser SQLite pre-filter) ────────────────────────────
# Curated `.shard` files indexed by SQLite for HUGINN's pre-filter pass. Discrete
# {-1, 0, 1} confidence — distinct from the float-confidence JSON shard store.
FACTS_DIR        = os.environ.get("NOVA_FACTS_DIR",        str(_DATA_ROOT /"facts"))
FACTS_INDEX_FILE = os.environ.get("NOVA_FACTS_INDEX_FILE", str(_DATA_ROOT /"facts_index.db"))

# ── Wiki layer ────────────────────────────────────────────────────────────────
WIKI_DIR          = os.environ.get("NOVA_WIKI_DIR",     str(_DATA_ROOT /"wiki"))
WIKI_SCHEMA_FILE  = os.environ.get("NOVA_WIKI_SCHEMA",  str(_DATA_ROOT /"wiki_schema.json"))
WIKI_INDEX_FILE   = os.environ.get("NOVA_WIKI_INDEX",   str(_DATA_ROOT /"wiki_index.json"))
WIKI_ROUTING_MODEL   = os.environ.get("NOVA_WIKI_ROUTING_MODEL",   "claude-haiku-4-5-20251001")
WIKI_SYNTHESIS_MODEL = os.environ.get("NOVA_WIKI_SYNTHESIS_MODEL", "claude-sonnet-4-6")

# ── Embedding integrity (VectorPin) ──────────────────────────────────────────
# HMAC-SHA256 key for signing/verifying embedding vectors.
# Unset → signing disabled; existing deployments are unaffected.
NOVA_EMBEDDING_HMAC_KEY = os.environ.get("NOVA_EMBEDDING_HMAC_KEY", "")
EMBEDDING_INTEGRITY_LOG = os.environ.get(
    "NOVA_EMBEDDING_INTEGRITY_LOG", str(_DATA_ROOT /"embedding_integrity.jsonl")
)

# ── External retrieval deliberation pipeline ──────────────────────────────────
# Fires when HUGINN internal retrieval confidence falls below threshold.
NOVA_EXTERNAL_RETRIEVAL_THRESHOLD = float(os.environ.get("NOVA_EXTERNAL_RETRIEVAL_THRESHOLD", "0.6"))
NOVA_EXTERNAL_COST_CAP            = float(os.environ.get("NOVA_EXTERNAL_COST_CAP",            "0.10"))
NOVA_EXTERNAL_RETRIEVAL_TIMEOUT   = float(os.environ.get("NOVA_EXTERNAL_RETRIEVAL_TIMEOUT",   "30.0"))
NOVA_EXTERNAL_ARBITER_MODEL       = os.environ.get("NOVA_EXTERNAL_ARBITER_MODEL",             "claude-sonnet-4-6")
NOVA_EXTERNAL_ARBITER_TIMEOUT     = float(os.environ.get("NOVA_EXTERNAL_ARBITER_TIMEOUT",     "90.0"))

# ── Skill verification layer ──────────────────────────────────────────────────
# SQLite audit log for HITL lifecycle events (irreversible.request/decision/executed,
# capability.denied) and post-session biconditional checks.
SKILL_AUDIT_LOG_FILE = os.environ.get(
    "NOVA_SKILL_AUDIT_LOG", str(_DATA_ROOT /"skill_audit.db")
)
# NOVA_HITL_BROKER: "interactive" (terminal prompt, dev default) | "policy" (always-deny)
HITL_BROKER    = os.environ.get("NOVA_HITL_BROKER",    "interactive")
HITL_TIMEOUT_S = int(os.environ.get("NOVA_HITL_TIMEOUT_S", "30"))
