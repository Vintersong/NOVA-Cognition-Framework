"""
nova_hotpool.py — Python ctypes bridge to nova_hotpool.so.

Provides a pure-Python FNV-1a 32-bit hash, ctypes wiring for the 4 exported
C functions, and a KIND_MAP matching config.py MEMORY_KIND_DECAY_RATES.

Build the shared library before importing:
    gcc -O2 -shared -fPIC -o _nova_hotpool.so nova_hotpool.c

The library is named _nova_hotpool.so to avoid shadowing this module in
Python's import path (nova_hotpool.so would be loaded as a Python extension
instead of this file).

This module is an isolated cache experiment — it does NOT import nova_server.py.
"""

from __future__ import annotations

import ctypes
from ctypes import c_float, c_uint8, c_uint32, c_uint64
from pathlib import Path

_SO_PATH = Path(__file__).parent / "_nova_hotpool.so"

try:
    _lib = ctypes.CDLL(str(_SO_PATH))
    _HOTPOOL_AVAILABLE = True
except OSError:
    _lib = None
    _HOTPOOL_AVAILABLE = False


class _HotPoolStats(ctypes.Structure):
    _fields_ = [
        ("hits",      c_uint64),
        ("misses",    c_uint64),
        ("evictions", c_uint64),
    ]


if _HOTPOOL_AVAILABLE:
    _lib.hotpool_lookup.argtypes  = [c_uint32]
    _lib.hotpool_lookup.restype   = c_float

    _lib.hotpool_insert.argtypes  = [c_uint32, c_float, c_uint8]
    _lib.hotpool_insert.restype   = None

    _lib.hotpool_stats.argtypes   = []
    _lib.hotpool_stats.restype    = _HotPoolStats

    _lib.hotpool_reset.argtypes   = []
    _lib.hotpool_reset.restype    = None


# KIND_MAP — indices match MEMORY_KIND_DECAY_RATES definition order in config.py.
KIND_MAP: dict[str, int] = {
    "session":      0,   # decay rate 0.10
    "event":        1,   # decay rate 0.07
    "reflection":   2,   # decay rate 0.05 (default)
    "research":     3,   # decay rate 0.03
    "project":      4,   # decay rate 0.02
    "decision":     5,   # decay rate 0.015
    "architecture": 6,   # decay rate 0.015
}


def fnv32(shard_id: str) -> int:
    """FNV-1a 32-bit hash of a UTF-8 encoded string.

    Offset basis: 2166136261 (0x811c9dc5). Prime: 16777619 (0x01000193).
    XOR-then-multiply per byte, masked to 32 bits after each multiply.
    """
    h = 2166136261
    for byte in shard_id.encode("utf-8"):
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def lookup(shard_id: str) -> float | None:
    """Return cached confidence for shard_id, or None on a miss."""
    if not _HOTPOOL_AVAILABLE:
        return None
    result = _lib.hotpool_lookup(c_uint32(fnv32(shard_id)))
    return None if result < 0.0 else float(result)


def insert(shard_id: str, confidence: float, kind: str) -> None:
    """Insert a shard into the hot pool (evicts lowest confidence if full)."""
    if not _HOTPOOL_AVAILABLE:
        return
    _lib.hotpool_insert(
        c_uint32(fnv32(shard_id)),
        c_float(confidence),
        c_uint8(KIND_MAP.get(kind, KIND_MAP["reflection"])),
    )


def stats() -> dict[str, int]:
    """Return hit/miss/eviction counters as a plain dict."""
    if not _HOTPOOL_AVAILABLE:
        return {"hits": 0, "misses": 0, "evictions": 0}
    s = _lib.hotpool_stats()
    return {"hits": s.hits, "misses": s.misses, "evictions": s.evictions}


def reset() -> None:
    """Zero all pool slots and counters. Intended for tests and bench setup."""
    if not _HOTPOOL_AVAILABLE:
        return
    _lib.hotpool_reset()


__all__ = [
    "lookup",
    "insert",
    "stats",
    "reset",
    "fnv32",
    "KIND_MAP",
    "_HOTPOOL_AVAILABLE",
]
