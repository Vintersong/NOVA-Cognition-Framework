from __future__ import annotations

from pathlib import Path

import pytest

from shard_parser import ShardDB, ShardParser


def _write_shard(dir_: Path, name: str, *, topic: str, content: str, confidence: int = 1) -> Path:
    path = dir_ / f"{name}.shard"
    text = (
        f"@@id: {name}\n"
        f"@@topic: {topic}\n"
        "@@tier: personal\n"
        f"@@confidence: {confidence}\n"
        "@@decay_rate: 0.0\n"
        "@@links:\n"
        "@@timestamp: 2026-05-08T00:00:00+00:00\n"
        "---\n"
        f"{content}\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


def test_search_filters_by_default_confidence(tmp_path: Path) -> None:
    db = ShardDB(tmp_path / "facts.db")
    confirmed = ShardParser.parse(_write_shard(tmp_path, "a", topic="memory model", content="...", confidence=1))
    contradicted = ShardParser.parse(_write_shard(tmp_path, "b", topic="memory model", content="...", confidence=-1))
    db.upsert(confirmed)
    db.upsert(contradicted)

    results = db.search("memory")
    ids = [r["id"] for r in results]
    assert "a" in ids
    assert "b" not in ids  # default confidence=1 excludes contradicted
    db.close()


def test_search_matches_topic_or_content(tmp_path: Path) -> None:
    db = ShardDB(tmp_path / "facts.db")
    db.upsert(ShardParser.parse(_write_shard(tmp_path, "a", topic="ravens", content="HUGINN flies first")))
    db.upsert(ShardParser.parse(_write_shard(tmp_path, "b", topic="other", content="muninn reranks deeper")))
    db.upsert(ShardParser.parse(_write_shard(tmp_path, "c", topic="unrelated", content="cats and dogs")))

    huginn_hits = [r["id"] for r in db.search("huginn")]
    muninn_hits = [r["id"] for r in db.search("muninn")]
    assert huginn_hits == ["a"]
    assert muninn_hits == ["b"]
    db.close()


def test_search_returns_empty_for_empty_query(tmp_path: Path) -> None:
    db = ShardDB(tmp_path / "facts.db")
    db.upsert(ShardParser.parse(_write_shard(tmp_path, "a", topic="x", content="y")))
    assert db.search("") == []
    assert db.search("   ") == []
    db.close()


def test_rebuild_from_dir_indexes_all_shards(tmp_path: Path) -> None:
    facts_root = tmp_path / "facts"
    facts_root.mkdir()
    nested = facts_root / "examples"
    nested.mkdir()
    _write_shard(facts_root, "top", topic="root level", content="...")
    _write_shard(nested, "deep", topic="nested level", content="...")

    db = ShardDB(tmp_path / "facts.db")
    indexed = db.rebuild_from_dir(facts_root)
    assert indexed == 2

    all_ids = sorted(r["id"] for r in db.search("level", confidence=None))
    assert all_ids == ["deep", "top"]
    db.close()


def test_search_facts_library_call_returns_empty_when_no_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_facts should be safe to call when neither the DB nor the dir exists."""
    import facts as facts_module
    monkeypatch.setattr(facts_module, "FACTS_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(facts_module, "FACTS_INDEX_FILE", str(tmp_path / "missing.db"))
    assert facts_module.search_facts("anything") == []


@pytest.mark.asyncio
async def test_facts_tools_honour_permission_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    """When NOVA_DENIED_TOOLS blocks a facts tool, the handler returns the
    denial payload instead of touching the SQLite index. Catches the gap
    Codex review flagged where _ALL_TOOL_NAMES inclusion alone wasn't
    enough to gate execution."""
    import json
    import facts as facts_module
    import permissions

    class _DummyMCP:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def tool(self, *, name: str, **_: object):
            def decorator(fn):
                self.tools[name] = fn
                return fn
            return decorator

    mcp = _DummyMCP()
    facts_module.register_facts_tools(mcp)

    ctx = permissions.ToolPermissionContext.from_iterables(
        deny_tools=["nova_facts_search", "nova_facts_rebuild"],
    )
    monkeypatch.setattr(permissions, "_active", ctx)

    search_out = await mcp.tools["nova_facts_search"](facts_module.FactsSearchInput(query="x"))
    rebuild_out = await mcp.tools["nova_facts_rebuild"](facts_module.FactsRebuildInput())

    assert "not permitted" in json.loads(search_out)["error"]
    assert "not permitted" in json.loads(rebuild_out)["error"]
