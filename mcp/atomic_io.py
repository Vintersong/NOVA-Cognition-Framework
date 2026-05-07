"""
atomic_io.py — Crash-safe JSON / text writers for NOVA persistent state.

A direct json.dump(open(path, "w"), ...) corrupts the target file if the
process crashes between truncate and final flush. Every persistent NOVA
artifact (shards, index, graph, summary index/markdown, wiki index, nidhogg
manifest) is read again on next startup, so a truncated write is data loss.

These helpers write to a sibling tmp file in the same directory, fsync, then
os.replace onto the destination. os.replace is atomic on POSIX and on Windows
(when source and target sit on the same filesystem, which they always do here
because tmp is created in the same directory).

Concurrent-writer protection (filelock.FileLock) is the caller's responsibility
— these helpers only guarantee crash atomicity, not mutual exclusion.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | Path, data: Any, *, indent: int | None = 2) -> None:
    target = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(target)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    target = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(target)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".txt", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
