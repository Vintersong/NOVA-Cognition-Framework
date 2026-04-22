"""
config.py — Centralised environment variable configuration for NOVA.

All modules import from here.  Changing a default means changing one line.
No object construction here — this is pure env-var reading.
"""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent


def parse_bool_env(key: str, default: bool = False) -> bool:
    """Parse a boolean env var using common truthy values."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# ── Paths ────────────────────────────────────────────────────────────────────
SHARD_DIR      = os.environ.get("NOVA_SHARD_DIR",     str(_REPO_ROOT / "shards"))
INDEX_FILE     = os.environ.get("NOVA_INDEX_FILE",    str(_REPO_ROOT / "shard_index.json"))
GRAPH_FILE     = os.environ.get("NOVA_GRAPH_FILE",    str(_REPO_ROOT / "shard_graph.json"))
SUMMARY_INDEX_FILE = os.environ.get("NOVA_SUMMARY_INDEX_FILE", str(_REPO_ROOT / "summary_index.json"))
SUMMARY_MARKDOWN_FILE = os.environ.get("NOVA_SUMMARY_MARKDOWN_FILE", str(_REPO_ROOT / "summary_index.md"))
USAGE_LOG_FILE = os.environ.get("NOVA_USAGE_LOG",     str(_REPO_ROOT / "nova_usage.jsonl"))
SESSION_STORE_DIR = os.environ.get(
    "NOVA_SESSION_STORE_DIR", str(_REPO_ROOT / "nova_sessions")
)

# ── Context window / fragment limits ─────────────────────────────────────────
MAX_FRAGMENTS = int(os.environ.get("NOVA_MAX_FRAGMENTS", "10"))

# ── Maintenance policy ────────────────────────────────────────────────────────
COMPACT_THRESHOLD   = int(os.environ.get("NOVA_COMPACT_THRESHOLD", "30"))
COMPACT_KEEP_RECENT = int(os.environ.get("NOVA_COMPACT_KEEP",      "15"))
DECAY_RATE          = float(os.environ.get("NOVA_DECAY_RATE",      "0.05"))
DECAY_INTERVAL_DAYS = int(os.environ.get("NOVA_DECAY_DAYS",        "7"))
MERGE_SIMILARITY_THRESHOLD = float(os.environ.get("NOVA_MERGE_THRESHOLD", "0.85"))

# ── Confidence bands ─────────────────────────────────────────────────────────
CONFIDENCE_LOW_THRESHOLD = float(os.environ.get("NOVA_CONFIDENCE_LOW", "0.4"))

# ── Shard age classification ──────────────────────────────────────────────────
RECENT_ACCESS_DAYS = int(os.environ.get("NOVA_RECENT_DAYS", "3"))
STALE_ACCESS_DAYS  = int(os.environ.get("NOVA_STALE_DAYS",  "14"))

# ── Norse Pantheon ─────────────────────────────────────────────────────────────
HUGINN_CONFIDENCE_THRESHOLD = float(os.environ.get("HUGINN_CONFIDENCE_THRESHOLD", "0.7"))
NOTT_COUNT_THRESHOLD        = int(os.environ.get("NOTT_COUNT_THRESHOLD",         "100"))

# ── Ravens LLM config ────────────────────────────────────────────────────────
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
HUGINN_MODEL      = os.environ.get("HUGINN_MODEL",      "claude-haiku-4-5-20251001")
MUNINN_MODEL      = os.environ.get("MUNINN_MODEL",      "claude-sonnet-4-6")
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL",      "gemini-2.5-flash")

# ── Input validation patterns ──────────────────────────────────────────────────
# Session IDs are persisted as filenames: keep strict and portable.
# 1-128 chars total, start alnum, then alnum / dot / underscore / dash.
SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"

# ── Wiki layer ────────────────────────────────────────────────────────────────
WIKI_DIR          = os.environ.get("NOVA_WIKI_DIR",     str(_REPO_ROOT / "wiki"))
WIKI_SCHEMA_FILE  = os.environ.get("NOVA_WIKI_SCHEMA",  str(_REPO_ROOT / "wiki_schema.json"))
WIKI_INDEX_FILE   = os.environ.get("NOVA_WIKI_INDEX",   str(_REPO_ROOT / "wiki_index.json"))
WIKI_ROUTING_MODEL   = os.environ.get("NOVA_WIKI_ROUTING_MODEL",   "claude-haiku-3-5")
WIKI_SYNTHESIS_MODEL = os.environ.get("NOVA_WIKI_SYNTHESIS_MODEL", "claude-sonnet-4-6")
