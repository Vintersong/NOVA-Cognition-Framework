"""
nova_hotpool.py — 8-slot L1 confidence cache for NOVA shard retrieval.

Two backends, same interface:
  C backend  — loads _nova_hotpool.so / _nova_hotpool.dll when present.
               Build: gcc -O2 -shared -fPIC -o mcp/_nova_hotpool.so mcp/nova_hotpool.c
               Windows: gcc -O2 -shared -o mcp/_nova_hotpool.dll mcp/nova_hotpool.c
  Python backend — pure-Python fallback, activates automatically when the
               shared library is absent. Same 8-slot, lowest-confidence-eviction
               policy; uses shard_id strings as keys directly (no FNV needed).

ravens.py imports lookup/insert and checks _HOTPOOL_AVAILABLE. Both backends
expose the same four public functions so callers need no conditional logic.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import c_float, c_uint8, c_uint32, c_uint64
from pathlib import Path

# ── Library discovery (platform-aware) ──────────────────────────────────────

def _find_lib() -> Path | None:
    base = Path(__file__).parent
    candidates = [
        base / "_nova_hotpool.so",   # Linux / macOS
        base / "_nova_hotpool.dll",  # Windows
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

_lib_path = _find_lib()

try:
    _lib = ctypes.CDLL(str(_lib_path)) if _lib_path else None
    _C_AVAILABLE = _lib is not None
except OSError:
    _lib = None
    _C_AVAILABLE = False


# ── ctypes wiring (only when C library loaded) ───────────────────────────────

class _HotPoolStats(ctypes.Structure):
    _fields_ = [
        ("hits",      c_uint64),
        ("misses",    c_uint64),
        ("evictions", c_uint64),
    ]


if _C_AVAILABLE:
    _lib.hotpool_lookup.argtypes  = [c_uint32]
    _lib.hotpool_lookup.restype   = c_float

    _lib.hotpool_insert.argtypes  = [c_uint32, c_float, c_uint8]
    _lib.hotpool_insert.restype   = None

    _lib.hotpool_stats.argtypes   = []
    _lib.hotpool_stats.restype    = _HotPoolStats

    _lib.hotpool_reset.argtypes   = []
    _lib.hotpool_reset.restype    = None


# ── Pure-Python fallback (8-slot, lowest-confidence eviction) ────────────────

_POOL_SIZE = 8
# Each slot: {"id": str, "confidence": float, "kind": int} or None
_py_pool: list[dict | None] = [None] * _POOL_SIZE
_py_stats: dict[str, int] = {"hits": 0, "misses": 0, "evictions": 0}


def _py_lookup(shard_id: str) -> float | None:
    for slot in _py_pool:
        if slot is not None and slot["id"] == shard_id:
            _py_stats["hits"] += 1
            return slot["confidence"]
    _py_stats["misses"] += 1
    return None


def _py_insert(shard_id: str, confidence: float, kind: int) -> None:
    # Update existing slot if present
    for i, slot in enumerate(_py_pool):
        if slot is not None and slot["id"] == shard_id:
            _py_pool[i] = {"id": shard_id, "confidence": confidence, "kind": kind}
            return
    # Find an empty slot
    for i, slot in enumerate(_py_pool):
        if slot is None:
            _py_pool[i] = {"id": shard_id, "confidence": confidence, "kind": kind}
            return
    # All slots full — evict lowest confidence
    min_idx = min(
        (i for i, s in enumerate(_py_pool) if s is not None),
        key=lambda i: _py_pool[i]["confidence"],  # type: ignore[index]
    )
    _py_pool[min_idx] = {"id": shard_id, "confidence": confidence, "kind": kind}
    _py_stats["evictions"] += 1


def _py_stats_snapshot() -> dict[str, int]:
    return dict(_py_stats)


def _py_reset() -> None:
    for i in range(_POOL_SIZE):
        _py_pool[i] = None
    _py_stats["hits"] = 0
    _py_stats["misses"] = 0
    _py_stats["evictions"] = 0


# ── KIND_MAP ─────────────────────────────────────────────────────────────────

KIND_MAP: dict[str, int] = {
    "session":      0,
    "event":        1,
    "reflection":   2,
    "research":     3,
    "project":      4,
    "decision":     5,
    "architecture": 6,
}


# ── FNV-1a (used by C backend; kept for callers that need the hash) ──────────

def fnv32(shard_id: str) -> int:
    """FNV-1a 32-bit hash of a UTF-8 encoded string."""
    h = 2166136261
    for byte in shard_id.encode("utf-8"):
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return h


# ── Public API ────────────────────────────────────────────────────────────────

_HOTPOOL_AVAILABLE = True  # always True — Python fallback guarantees availability


def lookup(shard_id: str) -> float | None:
    """Return cached confidence for shard_id, or None on a miss."""
    if _C_AVAILABLE:
        result = _lib.hotpool_lookup(c_uint32(fnv32(shard_id)))
        return None if result < 0.0 else float(result)
    return _py_lookup(shard_id)


def insert(shard_id: str, confidence: float, kind: str) -> None:
    """Insert a shard into the hot pool (evicts lowest confidence slot if full)."""
    kind_idx = KIND_MAP.get(kind, KIND_MAP["reflection"])
    if _C_AVAILABLE:
        _lib.hotpool_insert(c_uint32(fnv32(shard_id)), c_float(confidence), c_uint8(kind_idx))
    else:
        _py_insert(shard_id, confidence, kind_idx)


def stats() -> dict[str, int]:
    """Return hit/miss/eviction counters as a plain dict."""
    if _C_AVAILABLE:
        s = _lib.hotpool_stats()
        return {"hits": s.hits, "misses": s.misses, "evictions": s.evictions}
    return _py_stats_snapshot()


def reset() -> None:
    """Zero all pool slots and counters."""
    if _C_AVAILABLE:
        _lib.hotpool_reset()
    else:
        _py_reset()


def backend() -> str:
    """Return which backend is active: 'c' or 'python'."""
    return "c" if _C_AVAILABLE else "python"


__all__ = [
    "lookup",
    "insert",
    "stats",
    "reset",
    "fnv32",
    "backend",
    "KIND_MAP",
    "_HOTPOOL_AVAILABLE",
    "_C_AVAILABLE",
]
