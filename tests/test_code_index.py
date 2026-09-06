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

    # Two module-level runs: the leading docstring/import/constant block,
    # and the blank-line gap between top_level_func and class Foo.
    assert set(by_symbol) == {"<module>", "<module>#2", "top_level_func", "Foo"}
    assert by_symbol["<module>"]["kind"] == "module_header"
    assert by_symbol["<module>#2"]["kind"] == "module_header"
    assert by_symbol["top_level_func"]["kind"] == "function"
    assert by_symbol["Foo"]["kind"] == "class"

    assert by_symbol["<module>"]["start_line"] == 1
    assert by_symbol["<module>"]["end_line"] == 6
    assert by_symbol["<module>#2"]["start_line"] == 10
    assert by_symbol["<module>#2"]["end_line"] == 11

    assert by_symbol["top_level_func"]["start_line"] == 7
    assert by_symbol["top_level_func"]["end_line"] == 9

    # Class chunk spans the whole body, including both methods — methods are
    # not split into their own chunks (the fixture is well under
    # _MAX_CHUNK_CHARS, so no recursion into method-level chunks happens).
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


def test_chunk_file_indexes_module_level_code_between_and_after_defs():
    """Regression test for the bug the Opus review caught: module-level code
    is not only ever a leading block. tool_registry.py's entire _REGISTRY
    sits after several top-level defs and must still get its own chunk."""
    source = (
        "import os\n"          # 1
        "\n"                   # 2
        "def helper():\n"      # 3
        "    return 1\n"       # 4
        "\n"                   # 5
        "MID_CONST = 2\n"      # 6
        "\n"                   # 7
        "def other():\n"       # 8
        "    return 2\n"       # 9
        "\n"                   # 10
        "TRAILING = 3\n"       # 11
    )
    chunks = _chunk_file(source)
    by_symbol = {c["symbol"]: c for c in chunks}

    assert by_symbol["helper"]["start_line"] == 3
    assert by_symbol["helper"]["end_line"] == 4
    assert by_symbol["other"]["start_line"] == 8
    assert by_symbol["other"]["end_line"] == 9

    # Three separate module-level runs: leading import (1-2), the blank
    # line + MID_CONST + blank line between the two defs (5-7), and the
    # blank line + TRAILING after the last def (10-11).
    module_chunks = {k: v for k, v in by_symbol.items() if k.startswith("<module>")}
    assert len(module_chunks) == 3
    ranges = sorted((c["start_line"], c["end_line"]) for c in module_chunks.values())
    assert ranges == [(1, 2), (5, 7), (10, 11)]


def test_chunk_node_recurses_into_oversized_function(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the bug the Opus review caught: an oversized
    top-level function (e.g. a register_*_tools factory) must not collapse
    into one chunk whose nested handlers are invisible to search."""
    monkeypatch.setattr(code_index, "_MAX_CHUNK_CHARS", 40)

    source = (
        "def register_things(mcp):\n"
        "    def handler_one(x):\n"
        "        return x + 1\n"
        "\n"
        "    def handler_two(x):\n"
        "        return x + 2\n"
    )
    chunks = _chunk_file(source)
    by_symbol = {c["symbol"]: c for c in chunks}

    assert "register_things.handler_one" in by_symbol
    assert "register_things.handler_two" in by_symbol
    assert by_symbol["register_things.handler_one"]["kind"] == "function"
    # The parent's own header (just the def line) should also be indexed.
    assert "register_things" in by_symbol
    assert by_symbol["register_things"]["kind"] == "function_header"


def test_chunk_node_keeps_whole_chunk_when_no_children_to_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_index, "_MAX_CHUNK_CHARS", 10)
    source = "def big():\n    x = 1\n    y = 2\n    return x + y\n"
    chunks = _chunk_file(source)
    assert len(chunks) == 1
    assert chunks[0]["symbol"] == "big"
    assert chunks[0]["kind"] == "function"


def test_oversized_module_level_run_is_size_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the same root cause as the recursion fix, applied
    to module-level code instead of a def/class: a huge module-level block
    (e.g. tool_registry.py's _REGISTRY dict) must not become one chunk that
    the embedder silently truncates."""
    monkeypatch.setattr(code_index, "_MAX_CHUNK_CHARS", 30)
    # 10 lines of ~11 chars each — well over the 30-char cap as one chunk.
    source = "\n".join(f"CONST_{i} = {i}" for i in range(10)) + "\n"

    chunks = _chunk_file(source)
    module_chunks = [c for c in chunks if c["kind"] == "module_header"]
    assert len(module_chunks) > 1

    # Every line must land in exactly one window, and windows must be
    # contiguous and cover the whole file with no overlap or gap.
    covered_lines: list[int] = []
    for c in sorted(module_chunks, key=lambda c: c["start_line"]):
        covered_lines.extend(range(c["start_line"], c["end_line"] + 1))
    assert covered_lines == list(range(1, 11))


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
    assert set(manifest["chunks"]) == {"a.py::foo:1"}

    file_path.write_text("def bar():\n    return 2\n", encoding="utf-8")
    refresh_code_index()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert set(manifest["chunks"]) == {"a.py::bar:1"}


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
    payload = json.loads(out)
    assert payload["status"] == "rejected"
    assert payload["code"] == "permission_denied"
    assert "not permitted" in payload["message"]
