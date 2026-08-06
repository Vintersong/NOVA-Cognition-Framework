from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import code_index
import permissions
from code_index import CodeSearchInput, _chunk_file, _search_chunks, refresh_code_index


FIXTURE_SOURCE = '''"""Module docstring."""
import os

CONST = 1


def top_level_func(x):
    """Docstring."""
    return x + 1


class Foo:
    def method_a(self):
        return 1

    def method_b(self):
        return 2
'''


# ═══════════════════════════════════════════════════════════
# AST chunker
# ═══════════════════════════════════════════════════════════

def test_chunk_file_boundaries_and_kinds():
    chunks = _chunk_file(FIXTURE_SOURCE)
    by_symbol = {c["symbol"]: c for c in chunks}

    assert set(by_symbol) == {"<module>", "top_level_func", "Foo"}
    assert by_symbol["<module>"]["kind"] == "module_header"
    assert by_symbol["top_level_func"]["kind"] == "function"
    assert by_symbol["Foo"]["kind"] == "class"

    # Module header covers everything before the first top-level def/class.
    assert by_symbol["<module>"]["start_line"] == 1
    assert by_symbol["<module>"]["end_line"] == 6

    assert by_symbol["top_level_func"]["start_line"] == 7
    assert by_symbol["top_level_func"]["end_line"] == 9

    # Class chunk spans the whole body, including both methods — methods are
    # not split into their own chunks.
    assert by_symbol["Foo"]["start_line"] == 12
    assert by_symbol["Foo"]["end_line"] == 17


def test_chunk_file_returns_empty_on_syntax_error():
    assert _chunk_file("def broken(:\n") == []


def test_chunk_file_with_no_top_level_defs_is_one_module_chunk():
    source = "import os\nCONST = 1\n"
    chunks = _chunk_file(source)
    assert len(chunks) == 1
    assert chunks[0]["symbol"] == "<module>"
    assert chunks[0]["start_line"] == 1
    assert chunks[0]["end_line"] == 2


# ═══════════════════════════════════════════════════════════
# Incremental refresh
# ═══════════════════════════════════════════════════════════

def _patch_index_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src"
    src.mkdir()
    manifest_file = tmp_path / "manifest.json"
    monkeypatch.setattr(code_index, "CODE_INDEX_ROOT", str(src))
    monkeypatch.setattr(code_index, "CODE_INDEX_MANIFEST_FILE", str(manifest_file))
    return src, manifest_file


def test_refresh_skips_unchanged_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, _ = _patch_index_paths(monkeypatch, tmp_path)
    (src / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(code_index, "generate_local_embedding", lambda t: calls.append(t) or [1.0, 0.0])

    result1 = refresh_code_index()
    assert result1["files_embedded"] == 1
    assert len(calls) > 0
    calls_after_first = len(calls)

    result2 = refresh_code_index()
    assert result2["files_embedded"] == 0
    assert result2["files_skipped"] == 1
    assert len(calls) == calls_after_first  # no new embed calls for an unchanged file


def test_refresh_reembeds_changed_file_and_replaces_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, manifest_file = _patch_index_paths(monkeypatch, tmp_path)
    file_path = src / "a.py"
    file_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(code_index, "generate_local_embedding", lambda t: [1.0, 0.0])

    refresh_code_index()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert set(manifest["chunks"]) == {"a.py::foo"}

    file_path.write_text("def bar():\n    return 2\n", encoding="utf-8")
    refresh_code_index()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert set(manifest["chunks"]) == {"a.py::bar"}


def test_refresh_prunes_deleted_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, manifest_file = _patch_index_paths(monkeypatch, tmp_path)
    file_path = src / "a.py"
    file_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(code_index, "generate_local_embedding", lambda t: [1.0, 0.0])

    refresh_code_index()
    file_path.unlink()
    refresh_code_index()

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest["files"] == {}
    assert manifest["chunks"] == {}


def test_refresh_skips_unparseable_file_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, _ = _patch_index_paths(monkeypatch, tmp_path)
    (src / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(code_index, "generate_local_embedding", lambda t: [1.0, 0.0])

    result = refresh_code_index()
    assert result["files_failed"] == 1
    assert result["files_embedded"] == 0


# ═══════════════════════════════════════════════════════════
# Query
# ═══════════════════════════════════════════════════════════

def test_search_chunks_ranks_by_cosine_similarity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "version": 1,
        "files": {},
        "chunks": {
            "a.py::near": {
                "file": "a.py", "symbol": "near", "kind": "function",
                "start_line": 1, "end_line": 2, "embedding": [1.0, 0.0],
            },
            "a.py::far": {
                "file": "a.py", "symbol": "far", "kind": "function",
                "start_line": 3, "end_line": 4, "embedding": [0.0, 1.0],
            },
        },
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(code_index, "CODE_INDEX_MANIFEST_FILE", str(manifest_file))

    ranked = _search_chunks([1.0, 0.0], top_n=5)
    assert [chunk["symbol"] for _, chunk in ranked] == ["near", "far"]
    assert ranked[0][0] == pytest.approx(1.0)
    assert ranked[1][0] == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════
# nova_code_search tool
# ═══════════════════════════════════════════════════════════

class _DummyMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, **_: object):
        def decorator(fn):
            self.tools[name] = fn
            return fn
        return decorator


def test_nova_code_search_returns_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, _ = _patch_index_paths(monkeypatch, tmp_path)
    (src / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(code_index, "generate_local_embedding", lambda t: [1.0, 0.0])
    refresh_code_index()

    mcp = _DummyMCP()
    code_index.register_code_index_tools(mcp, ctx=None)

    out = asyncio.run(mcp.tools["nova_code_search"](CodeSearchInput(query="foo")))
    payload = json.loads(out)
    assert payload["match_count"] == 1
    assert payload["matches"][0]["symbol"] == "foo"
    assert "def foo" in payload["matches"][0]["source"]


def test_nova_code_search_honours_permission_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _DummyMCP()
    code_index.register_code_index_tools(mcp, ctx=None)

    ctx_perm = permissions.ToolPermissionContext.from_iterables(deny_tools=["nova_code_search"])
    monkeypatch.setattr(permissions, "_active", ctx_perm)

    out = asyncio.run(mcp.tools["nova_code_search"](CodeSearchInput(query="x")))
    assert "not permitted" in json.loads(out)["error"]
