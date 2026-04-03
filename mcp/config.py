"""
config.py — Centralised environment variable configuration for NOVA.

All modules import from here.  Changing a default means changing one line.
No object construction here — this is pure env-var reading.
"""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent

# ── Paths ────────────────────────────────────────────────────────────────────
SHARD_DIR      = os.environ.get("NOVA_SHARD_DIR",     str(_REPO_ROOT / "shards"))
INDEX_FILE     = os.environ.get("NOVA_INDEX_FILE",    str(_REPO_ROOT / "shard_index.json"))
GRAPH_FILE     = os.environ.get("NOVA_GRAPH_FILE",    str(_REPO_ROOT / "shard_graph.json"))
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

# ── Norse Pantheon ─────────────────────────────────────────────────────────────
HUGINN_CONFIDENCE_THRESHOLD = float(os.environ.get("HUGINN_CONFIDENCE_THRESHOLD", "0.7"))
NOTT_COUNT_THRESHOLD        = int(os.environ.get("NOTT_COUNT_THRESHOLD",         "100"))
