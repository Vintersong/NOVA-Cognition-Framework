"""Tests for the hot pool cache and for ravens no longer scoring from it."""

from __future__ import annotations

import ctypes
import shutil
import subprocess
from ctypes import c_float, c_uint8, c_uint32
from pathlib import Path

import pytest

import nova_hotpool as hp

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def py_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hp, "_C_AVAILABLE", False)
    hp.reset()
    yield hp
    hp.reset()


@pytest.fixture
def c_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    so = tmp_path / "_nova_hotpool.so"
    subprocess.run(
        ["gcc", "-O2", "-shared", "-fPIC", "-o", str(so), str(REPO_ROOT / "mcp" / "nova_hotpool.c")],
        check=True,
    )
    lib = ctypes.CDLL(str(so))
    lib.hotpool_lookup.argtypes = [c_uint32]
    lib.hotpool_lookup.restype = c_float
    lib.hotpool_insert.argtypes = [c_uint32, c_float, c_uint8]
    lib.hotpool_insert.restype = None
    lib.hotpool_stats.argtypes = []
    lib.hotpool_stats.restype = hp._HotPoolStats
    lib.hotpool_reset.argtypes = []
    lib.hotpool_reset.restype = None
    monkeypatch.setattr(hp, "_lib", lib)
    monkeypatch.setattr(hp, "_C_AVAILABLE", True)
    monkeypatch.setattr(hp, "_c_owner", {})
    hp.reset()
    yield hp


@pytest.mark.parametrize("backend", ["py_backend", "c_backend"])
def test_reinsert_refreshes_value(backend, request) -> None:
    pool = request.getfixturevalue(backend)
    pool.insert("a", 0.9, "project")
    pool.insert("a", 0.3, "project")
    assert pool.lookup("a") == pytest.approx(0.3)


def test_c_hash_collision_reads_as_miss(c_backend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "fnv32", lambda shard_id: 42)  # force every id to collide
    hp.insert("a", 0.9, "project")
    hp.insert("b", 0.2, "project")
    assert hp.lookup("a") is None
    assert hp.lookup("b") == pytest.approx(0.2)


def test_local_retrieve_scores_from_index_not_cache(tmp_path: Path) -> None:
    import ravens

    huginn = ravens.Huginn(str(tmp_path), str(tmp_path / "usage.jsonl"))
    entry = {"guiding_question": "alpha topic", "confidence": 0.5, "tags": [], "meta": {}}
    before = dict(huginn._local_retrieve("alpha", {"s1": entry}, 5))
    hp.insert("s1", 1.0, "project")  # a stale cached value must not change the score
    after = dict(huginn._local_retrieve("alpha", {"s1": entry}, 5))
    hp.reset()
    assert after == before
