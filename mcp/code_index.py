"""
code_index.py — Semantic code search over NOVA's own MCP server source.

Kilo-style codebase indexing, adapted to NOVA's existing local-embedding
infrastructure: AST-chunks mcp/**/*.py at function/class granularity, embeds
each chunk with the local all-MiniLM-L6-v2 model (no API key needed), and
answers nova_code_search queries by cosine similarity over the resulting
manifest.

Pipeline (refresh_code_index, run on every SESSION_START):
    1. Walk mcp/**/*.py
    2. SHA256 each file — skip re-embedding unchanged files (idempotent)
    3. AST-chunk changed/new files (see _chunk_file / _chunk_node):
         - one chunk per top-level def/class, unless its source exceeds
           _MAX_CHUNK_CHARS, in which case it's split into one chunk per
           immediate child def/method plus a "*_header" chunk for what's
           left over (recursing further if a child is itself oversized)
         - one or more "module_header" chunks covering every module-level
           line not inside a top-level def/class, wherever it falls in the
           file (not just before the first def/class)
    4. Embed each chunk via nova_embeddings_local.generate_local_embedding
    5. Write code_index_manifest.json — {version, files: {...}, chunks: {...}}
    6. Prune entries for files no longer on disk (handles deletes/renames)

Query (nova_code_search):
    Embeds the query text, cosine-ranks every manifest chunk, and reads the
    live file at the recorded line range for the returned snippet — so
    results always reflect current disk content even if the index is a
    session or two stale.

Registration:
    register_code_index_tools(mcp, ctx) — called once in nova_server.py

MCP Tools (1):
    nova_code_search — semantic search over mcp/ source code
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from atomic_io import atomic_write_json
from maintenance import cosine_similarity
from nova_embeddings_local import generate_local_embedding
from permissions import denial_payload, is_blocked
from tool_registry import nova_tool

logger = logging.getLogger(__name__)

# ── Paths (env-overridable, no changes to config.py required) ─────────────────
_REPO_ROOT = Path(__file__).parent.parent
CODE_INDEX_ROOT = os.environ.get("NOVA_CODE_INDEX_ROOT", str(_REPO_ROOT / "mcp"))
CODE_INDEX_MANIFEST_FILE = os.environ.get(
    "NOVA_CODE_INDEX_MANIFEST", str(_REPO_ROOT / "code_index_manifest.json")
)

_EXCLUDED_DIR_NAMES = {"__pycache__", ".git"}

# Character-count proxy for all-MiniLM-L6-v2's ~256 word-piece limit. Text
# beyond this is silently truncated at embed time, so any single AST node
# whose source exceeds it gets recursively split (see _chunk_node) instead
# of embedded as one mostly-invisible chunk. A real tokenizer count would be
# more precise, but would force _chunk_file to depend on the (lazily loaded,
# possibly absent) embedding model just to decide chunk boundaries — this
# keeps chunking pure and independently testable. ~3.5 chars/word-piece is a
# conservative estimate for code (identifiers/symbols split more than prose).
_MAX_CHUNK_CHARS = 800

_EMPTY_MANIFEST: dict = {"version": 1, "files": {}, "chunks": {}}


# ═══════════════════════════════════════════════════════════
# INPUT SCHEMA
# ═══════════════════════════════════════════════════════════

class CodeSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., min_length=1, description="Natural-language or symbol-name search query")
    top_n: int = Field(default=5, ge=1, le=20, description="Maximum number of matching chunks to return")


# ═══════════════════════════════════════════════════════════
# MANIFEST I/O
# ═══════════════════════════════════════════════════════════

def _load_manifest() -> dict:
    if not os.path.exists(CODE_INDEX_MANIFEST_FILE):
        return {"version": 1, "files": {}, "chunks": {}}
    try:
        with open(CODE_INDEX_MANIFEST_FILE, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return {"version": 1, "files": {}, "chunks": {}}
    manifest.setdefault("version", 1)
    manifest.setdefault("files", {})
    manifest.setdefault("chunks", {})
    return manifest


def _save_manifest(manifest: dict) -> None:
    with FileLock(CODE_INDEX_MANIFEST_FILE + ".lock", timeout=5):
        atomic_write_json(CODE_INDEX_MANIFEST_FILE, manifest)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ═══════════════════════════════════════════════════════════
# AST CHUNKING
# ═══════════════════════════════════════════════════════════

def _node_span(node: ast.AST) -> tuple[int, int]:
    """1-indexed inclusive (start, end) line range for a def/class node,
    including any decorators (which sit above ``node.lineno``)."""
    decorators = getattr(node, "decorator_list", [])
    start = min([node.lineno] + [d.lineno for d in decorators]) if decorators else node.lineno
    end = node.end_lineno or node.lineno
    return start, end


def _uncovered_runs(start: int, end: int, covered: set[int]) -> list[tuple[int, int]]:
    """Contiguous (start, end) sub-ranges of [start, end] whose lines are
    not in `covered`. Used to chunk module/function/class-level code that
    sits *between* or *after* nested def/class nodes, not just before the
    first one."""
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for ln in range(start, end + 1):
        if ln in covered:
            if run_start is not None:
                runs.append((run_start, ln - 1))
                run_start = None
        elif run_start is None:
            run_start = ln
    if run_start is not None:
        runs.append((run_start, end))
    return runs


def _split_by_size(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Greedily split [start, end] into windows that each stay within
    _MAX_CHUNK_CHARS. Used for plain (non-def/class) line runs — module-level
    code, or a header run left over after def/class recursion — where there's
    no nested structure to recurse into, only a flat span to window."""
    windows: list[tuple[int, int]] = []
    window_start = start
    length = 0
    for ln in range(start, end + 1):
        line_len = len(lines[ln - 1]) + 1  # +1 for the eventual join newline
        if length > 0 and length + line_len > _MAX_CHUNK_CHARS:
            windows.append((window_start, ln - 1))
            window_start = ln
            length = 0
        length += line_len
    windows.append((window_start, end))
    return windows


def _emit_run_chunks(
    lines: list[str], runs: list[tuple[int, int]], kind: str, base_symbol: str
) -> list[dict]:
    """Turn a list of (start, end) line runs into chunk dicts, size-splitting
    any run that exceeds _MAX_CHUNK_CHARS into further windows. Shared by
    module-level runs (_chunk_file) and def/class header runs (_chunk_node)."""
    chunks: list[dict] = []
    index = 0
    for run_start, run_end in runs:
        text = "\n".join(lines[run_start - 1: run_end])
        windows = (
            [(run_start, run_end)]
            if len(text) <= _MAX_CHUNK_CHARS
            else _split_by_size(lines, run_start, run_end)
        )
        for w_start, w_end in windows:
            index += 1
            symbol = base_symbol if index == 1 else f"{base_symbol}#{index}"
            chunks.append({
                "symbol": symbol,
                "kind": kind,
                "start_line": w_start,
                "end_line": w_end,
            })
    return chunks


def _chunk_node(node: ast.AST, lines: list[str], qualname: str) -> list[dict]:
    """Chunk one def/class node, recursing into its immediate child
    defs/methods when its own source exceeds _MAX_CHUNK_CHARS.

    This is what keeps large factories (e.g. a ``register_*_tools`` function
    whose real content is a dozen nested tool handlers) and large classes
    searchable at their actual symbol granularity instead of collapsing into
    one chunk that only the first ~20-30 lines of ever get embedded.
    """
    start, end = _node_span(node)
    text = "\n".join(lines[start - 1: end])
    is_class = isinstance(node, ast.ClassDef)

    if len(text) <= _MAX_CHUNK_CHARS:
        return [{
            "symbol": qualname,
            "kind": "class" if is_class else "function",
            "start_line": start,
            "end_line": end,
        }]

    child_defs = [
        child for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if not child_defs:
        # No way to split further — keep the whole (embed-truncated) chunk.
        return [{
            "symbol": qualname,
            "kind": "class" if is_class else "function",
            "start_line": start,
            "end_line": end,
        }]

    chunks: list[dict] = []
    covered: set[int] = set()
    for child in child_defs:
        c_start, c_end = _node_span(child)
        for ln in range(c_start, c_end + 1):
            covered.add(ln)
        chunks.extend(_chunk_node(child, lines, f"{qualname}.{child.name}"))

    header_kind = "class_header" if is_class else "function_header"
    header_runs = _uncovered_runs(start, end, covered)
    chunks.extend(_emit_run_chunks(lines, header_runs, header_kind, qualname))

    return chunks


def _chunk_file(source: str) -> list[dict]:
    """
    AST-chunk one file's source into top-level def/class chunks (recursing
    into oversized ones — see _chunk_node) plus one or more module-header
    chunks covering every module-level line not inside a top-level def/class,
    wherever it falls in the file (imports and docstring at the top, but also
    constants or setup code between or after top-level defs).

    Returns [{"symbol", "kind", "start_line", "end_line"}, ...], 1-indexed
    inclusive line ranges matching ast's lineno/end_lineno. Returns [] if the
    source fails to parse (e.g. a syntax error) or is empty.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return []

    top_defs = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]

    chunks: list[dict] = []
    covered: set[int] = set()
    for node in top_defs:
        start, end = _node_span(node)
        for ln in range(start, end + 1):
            covered.add(ln)
        chunks.extend(_chunk_node(node, lines, node.name))

    module_runs = _uncovered_runs(1, total_lines, covered)
    chunks.extend(_emit_run_chunks(lines, module_runs, "module_header", "<module>"))

    return chunks


# ═══════════════════════════════════════════════════════════
# REFRESH (indexer)
# ═══════════════════════════════════════════════════════════

def _iter_source_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in _EXCLUDED_DIR_NAMES for part in p.parts)
    )


def refresh_code_index() -> dict:
    """
    Incrementally rebuild the code index manifest.

    Hashes every .py file under CODE_INDEX_ROOT; unchanged files are skipped
    entirely (no re-embed). Changed or new files are re-chunked and
    re-embedded, replacing their old chunk entries. Files present in the
    manifest but missing from disk (deleted/renamed) are pruned.

    Called from the SESSION_START hook (server_context.py) in a background
    thread — safe to call directly too (e.g. tests, a manual refresh).
    """
    root = Path(CODE_INDEX_ROOT)
    if not root.exists():
        return {"status": "no_index_root", "root": str(root)}

    manifest = _load_manifest()
    files_meta: dict = manifest["files"]
    chunks_meta: dict = manifest["chunks"]

    on_disk = _iter_source_files(root)
    on_disk_relpaths = {_relpath(p, root) for p in on_disk}

    # Prune files no longer on disk (deletes/renames).
    for stale_relpath in set(files_meta) - on_disk_relpaths:
        for chunk_id in files_meta[stale_relpath].get("chunk_ids", []):
            chunks_meta.pop(chunk_id, None)
        del files_meta[stale_relpath]

    embedded = skipped = failed = 0

    for path in on_disk:
        relpath = _relpath(path, root)
        try:
            file_hash = _file_hash(path)
        except OSError:
            failed += 1
            continue

        existing = files_meta.get(relpath)
        if existing and existing.get("hash") == file_hash:
            skipped += 1
            continue

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            failed += 1
            continue

        chunk_specs = _chunk_file(source)
        if not chunk_specs:
            failed += 1
            continue

        lines = source.splitlines()
        new_chunk_ids: list[str] = []
        for spec in chunk_specs:
            text = "\n".join(lines[spec["start_line"] - 1: spec["end_line"]])
            embedding = generate_local_embedding(text)
            if embedding is None:
                continue
            # Suffix with start_line: qualnames aren't guaranteed unique
            # (duplicate top-level defs, multiple module_header runs).
            chunk_id = f"{relpath}::{spec['symbol']}:{spec['start_line']}"
            chunks_meta[chunk_id] = {
                "file": relpath,
                "symbol": spec["symbol"],
                "kind": spec["kind"],
                "start_line": spec["start_line"],
                "end_line": spec["end_line"],
                "embedding": embedding,
            }
            new_chunk_ids.append(chunk_id)

        if not new_chunk_ids:
            # Embedding model unavailable — don't record a hash we didn't
            # actually index, so the next refresh retries this file.
            failed += 1
            continue

        # Replace this file's old chunk entries now that the new ones exist.
        if existing:
            for chunk_id in existing.get("chunk_ids", []):
                if chunk_id not in new_chunk_ids:
                    chunks_meta.pop(chunk_id, None)

        files_meta[relpath] = {"hash": file_hash, "chunk_ids": new_chunk_ids}
        embedded += 1

    _save_manifest(manifest)
    return {
        "status": "ok",
        "files_scanned": len(on_disk),
        "files_embedded": embedded,
        "files_skipped": skipped,
        "files_failed": failed,
        "total_chunks": len(chunks_meta),
    }


def _relpath(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace(os.sep, "/")


# ═══════════════════════════════════════════════════════════
# QUERY
# ═══════════════════════════════════════════════════════════

def _search_chunks(query_embedding: list[float], top_n: int) -> list[tuple[float, dict]]:
    manifest = _load_manifest()
    scored: list[tuple[float, dict]] = []
    for chunk in manifest["chunks"].values():
        embedding = chunk.get("embedding")
        if not embedding:
            continue
        score = cosine_similarity(query_embedding, embedding)
        scored.append((score, chunk))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[:top_n]


def _read_snippet(relpath: str, start_line: int, end_line: int) -> str:
    path = Path(CODE_INDEX_ROOT) / relpath
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[start_line - 1: end_line])


# ═══════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═══════════════════════════════════════════════════════════

def register_code_index_tools(mcp, ctx) -> None:
    """Register the nova_code_search tool onto an existing MCPServer instance.
    Called once in nova_server.py after server init — same pattern as Nidhogg.

    Read-only and reversible (fs.read) — no capability gate needed, matching
    nova_huginn_candidates' pure pre-filter contract.
    """

    @nova_tool(mcp, name="nova_code_search")
    async def nova_code_search(params: CodeSearchInput) -> str:
        """
        Semantic search over NOVA's own mcp/ source code.

        Embeds the query with the local all-MiniLM-L6-v2 model and cosine-ranks
        it against every indexed function/class/module-header chunk (built by
        the SESSION_START background refresh — see code_index.refresh_code_index).
        Use this instead of Grep when you know *what* you're looking for
        conceptually but not the exact file or symbol name.

        Returns JSON:
          {
            "query": "<query>",
            "match_count": <int>,
            "matches": [
              {"file": "...", "symbol": "...",
               "kind": "function|class|module_header|function_header|class_header",
               "start_line": N, "end_line": N, "similarity_score": 0.XX, "source": "..."},
              ...
            ]
          }
        """
        if is_blocked("nova_code_search"):
            return denial_payload("nova_code_search")

        query = params.query.strip()
        query_embedding = generate_local_embedding(query)
        if query_embedding is None:
            return json.dumps({
                "status": "unavailable",
                "reason": "embedding model unavailable — install sentence-transformers",
            }, indent=2)

        ranked = _search_chunks(query_embedding, params.top_n)
        matches = [
            {
                "file": chunk["file"],
                "symbol": chunk["symbol"],
                "kind": chunk["kind"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "similarity_score": round(score, 4),
                "source": _read_snippet(chunk["file"], chunk["start_line"], chunk["end_line"]),
            }
            for score, chunk in ranked
        ]

        return json.dumps({
            "query": query,
            "match_count": len(matches),
            "matches": matches,
        }, indent=2)
