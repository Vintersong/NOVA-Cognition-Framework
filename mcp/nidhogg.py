"""
nidhogg.py — NOVA's document ingestion pipeline.

Nidhogg gnaws at the roots of Yggdrasil without pause.
This module ingests external documents into NOVA's shard graph with full
provenance tracking — no destructive rewrites, only additive nidhogg blocks.

Pipeline:
    1. Read file from intake/ (or explicit path)
    2. SHA256 hash → skip if already ingested (idempotent)
    3. Embed content via local all-MiniLM-L6-v2 (reuses warm model)
    4. Cosine match against shard embeddings → ranked candidates
    5. Haiku structured analysis — entities, concepts, relationships, contradictions
       (skipped gracefully if CLAUDE_API_KEY is absent)
    6. Append nidhogg block to each matched shard (file-locked via store.save_shard)
    7. Flag similarity >= MERGE_SIMILARITY_THRESHOLD as merge candidates for NÓTT

Architecture:
    - Reads from store.py / maintenance.py / nova_embeddings_local.py only
    - Never modifies existing shard fields — only appends top-level "nidhogg" list
    - Manifest (nidhogg_manifest.json) tracks ingested file hashes — idempotent

Registration:
    register_nidhogg_tools(mcp)  — called once in nova_server.py

MCP Tools (3):
    nidhogg_ingest   — ingest a single file by path
    nidhogg_scan     — scan intake/ and ingest all pending files
    nidhogg_status   — show manifest (what has been ingested)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from filelock import FileLock
from pydantic import BaseModel, Field, ConfigDict

from config import SHARD_DIR, MERGE_SIMILARITY_THRESHOLD
from maintenance import cosine_similarity
from nova_embeddings_local import generate_local_embedding
from permissions import is_blocked, denial_payload
from store import load_index, load_shard, save_shard

# ── Paths (env-overridable, no changes to config.py required) ─────────────────
_REPO_ROOT = Path(__file__).parent.parent
NIDHOGG_INTAKE_DIR = os.environ.get(
    "NIDHOGG_INTAKE_DIR", str(_REPO_ROOT / "intake")
)
NIDHOGG_MANIFEST_FILE = os.environ.get(
    "NIDHOGG_MANIFEST_FILE", str(_REPO_ROOT / "nidhogg_manifest.json")
)
NIDHOGG_SIMILARITY_THRESHOLD = float(
    os.environ.get("NIDHOGG_SIMILARITY_THRESHOLD", "0.55")
)
_ALLOWED_ROOTS_ENV = os.environ.get("NIDHOGG_ALLOWED_ROOTS", NIDHOGG_INTAKE_DIR)
NIDHOGG_ALLOWED_ROOTS = tuple(
    str(Path(root.strip()).resolve()) for root in _ALLOWED_ROOTS_ENV.split(",") if root.strip()
)

# ── Optional Haiku analysis — graceful no-op if key is absent ─────────────────
from config import CLAUDE_API_KEY as _CLAUDE_API_KEY, HUGINN_MODEL as _HAIKU_MODEL

# ── Supported plain-text extensions (no special parser needed) ────────────────
_TEXT_EXTENSIONS = {".txt", ".md", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml"}


# ═══════════════════════════════════════════════════════════
# INPUT SCHEMAS
# ═══════════════════════════════════════════════════════════

class NidhoggIngestInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    file_path: str = Field(..., min_length=1, description="Absolute or relative path to the file to ingest")
    source_type: str = Field(
        default="document",
        description="Source category: paper, article, book, note, code, document, etc.",
    )
    top_n: int = Field(
        default=5, ge=1, le=20,
        description="Maximum number of shards to annotate with this source",
    )


class NidhoggScanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    source_type: str = Field(default="document")
    top_n: int = Field(default=5, ge=1, le=20)


class NidhoggStatusInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# ═══════════════════════════════════════════════════════════
# MANIFEST — SHA256 cache (idempotent ingestion)
# ═══════════════════════════════════════════════════════════

def _load_manifest() -> dict:
    if not os.path.exists(NIDHOGG_MANIFEST_FILE):
        return {}
    try:
        with open(NIDHOGG_MANIFEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_manifest(manifest: dict) -> None:
    with FileLock(NIDHOGG_MANIFEST_FILE + ".lock", timeout=5):
        with open(NIDHOGG_MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ═══════════════════════════════════════════════════════════
# FILE READING
# ═══════════════════════════════════════════════════════════

def _read_file(path: str) -> str:
    """Read file content as plain text. PDF support requires pypdf."""
    ext = Path(path).suffix.lower()

    if ext == ".pdf":
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(path)
            return "\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except ImportError:
            return f"[PDF parsing requires pypdf: pip install pypdf] File: {path}"
        except Exception as e:
            return f"[PDF read error: {e}]"

    # Plain text for everything else
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Split on paragraph boundaries; fall back to hard split."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            current = para[:max_chars]

    if current:
        chunks.append(current)

    return chunks or [text[:max_chars]]


def _average_embedding(embeddings: list[list[float]]) -> list[float] | None:
    """Mean-pool a list of chunk embeddings into one document embedding."""
    if not embeddings:
        return None
    dim = len(embeddings[0])
    result = [0.0] * dim
    for emb in embeddings:
        for i, v in enumerate(emb):
            result[i] += v
    n = len(embeddings)
    return [v / n for v in result]


# ═══════════════════════════════════════════════════════════
# SHARD MATCHING
# ═══════════════════════════════════════════════════════════

def _match_shards(content_embedding: list[float], top_n: int) -> list[dict]:
    """
    Compare content embedding against all shard embeddings.
    Embeddings live in shard files (context.embedding), not in the index.
    Iterates index for shard IDs, loads each shard file for its embedding.
    Returns ranked matches above NIDHOGG_SIMILARITY_THRESHOLD.
    """
    index = load_index()
    candidates = []

    for shard_id, entry in index.items():
        # Skip archived / forgotten shards
        tags = entry.get("tags", [])
        if "archived" in tags or "forgotten" in tags:
            continue

        shard_path = os.path.join(SHARD_DIR, shard_id + ".json")
        if not os.path.exists(shard_path):
            continue

        try:
            with open(shard_path, "r", encoding="utf-8") as f:
                shard_data = json.load(f)
            shard_embedding = shard_data.get("context", {}).get("embedding")
            if not shard_embedding:
                continue

            score = cosine_similarity(content_embedding, shard_embedding)
            if score >= NIDHOGG_SIMILARITY_THRESHOLD:
                candidates.append({
                    "shard_id": shard_id,
                    "similarity_score": round(score, 4),
                    "merge_candidate": score >= MERGE_SIMILARITY_THRESHOLD,
                    "guiding_question": shard_data.get("guiding_question", ""),
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: x["similarity_score"], reverse=True)
    return candidates[:top_n]


# ═══════════════════════════════════════════════════════════
# HAIKU ANALYSIS (optional)
# ═══════════════════════════════════════════════════════════

def _haiku_analysis(content: str, guiding_question: str) -> dict:
    """
    Ask Haiku to extract structured knowledge from the document
    in the context of a matched shard's guiding question.
    Returns extracted dict. Falls back to empty dict if API key absent or call fails.
    """
    if not _CLAUDE_API_KEY:
        return {"analysis_skipped": "no CLAUDE_API_KEY"}

    try:
        import anthropic  # type: ignore
        client = anthropic.Anthropic(api_key=_CLAUDE_API_KEY)

        prompt = (
            f"You are analyzing a document to enrich a knowledge shard.\n\n"
            f"SHARD QUESTION: {guiding_question}\n\n"
            f"DOCUMENT (first 3000 chars):\n{content[:3000]}\n\n"
            "Extract the following as JSON with these exact keys:\n"
            "  entities: list of named people, orgs, tools, projects\n"
            "  concepts: list of key ideas, theories, methods\n"
            "  relationships: list of 'A relates to B' strings\n"
            "  contradictions: list of claims that conflict with the shard topic\n"
            "  summary: one sentence describing relevance to the shard question\n\n"
            "Return ONLY valid JSON, no markdown fences."
        )

        message = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        return json.loads(raw)

    except Exception as e:
        return {"analysis_error": str(e)}


# ═══════════════════════════════════════════════════════════
# NIDHOGG BLOCK WRITER
# ═══════════════════════════════════════════════════════════

def _append_nidhogg_block(
    shard_id: str,
    source_file: str,
    source_hash: str,
    source_type: str,
    similarity_score: float,
    merge_candidate: bool,
    analysis: dict,
) -> None:
    """
    Append a nidhogg provenance block to a shard.
    Uses store.load_shard / store.save_shard — file-locked, safe.
    Never modifies any existing shard field.
    """
    shard_data, filepath = load_shard(shard_id)

    block = {
        "source_file": os.path.basename(source_file),
        "source_path": source_file,
        "source_hash": source_hash,
        "source_type": source_type,
        "ingested_at": datetime.now().isoformat(),
        "similarity_score": similarity_score,
        "merge_candidate": merge_candidate,
        "extracted": {
            "entities": analysis.get("entities", []),
            "concepts": analysis.get("concepts", []),
            "relationships": analysis.get("relationships", []),
            "contradictions": analysis.get("contradictions", []),
        },
        "summary": analysis.get("summary", ""),
    }

    if "analysis_skipped" in analysis:
        block["analysis_note"] = analysis["analysis_skipped"]
    if "analysis_error" in analysis:
        block["analysis_note"] = f"error: {analysis['analysis_error']}"

    nidhogg_list = shard_data.setdefault("nidhogg", [])
    nidhogg_list.append(block)

    save_shard(filepath, shard_data)


# ═══════════════════════════════════════════════════════════
# CORE INGEST FUNCTION
# ═══════════════════════════════════════════════════════════

def _ingest_file(file_path: str, source_type: str, top_n: int) -> dict:
    """
    Full ingestion pipeline for a single file.
    Returns a result dict describing what was done.
    """
    try:
        path = _resolve_allowed_ingest_path(file_path)
    except ValueError as exc:
        return {
            "status": "error",
            "code": "path_not_allowed",
            "message": str(exc),
            "allowed_roots": list(NIDHOGG_ALLOWED_ROOTS),
        }

    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}

    # SHA256 check — idempotent
    file_hash = _file_hash(path)
    manifest = _load_manifest()
    if file_hash in manifest:
        return {
            "status": "skipped",
            "reason": "already ingested",
            "file": path,
            "hash": file_hash,
            "previously_ingested_at": manifest[file_hash].get("ingested_at"),
        }

    # Read and embed
    content = _read_file(path)
    if not content.strip():
        return {"error": f"File is empty or unreadable: {path}"}

    chunks = _chunk_text(content)
    chunk_embeddings = [generate_local_embedding(c) for c in chunks]
    valid_embeddings = [e for e in chunk_embeddings if e is not None]

    if not valid_embeddings:
        return {
            "status": "no_embedding",
            "reason": "embedding model unavailable — install sentence-transformers",
            "file": path,
        }

    doc_embedding = _average_embedding(valid_embeddings)

    # Match shards
    matches = _match_shards(doc_embedding, top_n)
    if not matches:
        # Record in manifest so we don't retry endlessly
        manifest[file_hash] = {
            "file": path,
            "source_type": source_type,
            "ingested_at": datetime.now().isoformat(),
            "matched_shards": [],
            "note": "no shards above similarity threshold",
        }
        _save_manifest(manifest)
        return {
            "status": "no_matches",
            "file": path,
            "threshold": NIDHOGG_SIMILARITY_THRESHOLD,
            "shards_scanned": len(load_index()),
        }

    # Annotate matched shards
    annotated = []
    for match in matches:
        shard_id = match["shard_id"]
        analysis = _haiku_analysis(content, match["guiding_question"])
        _append_nidhogg_block(
            shard_id=shard_id,
            source_file=path,
            source_hash=file_hash,
            source_type=source_type,
            similarity_score=match["similarity_score"],
            merge_candidate=match["merge_candidate"],
            analysis=analysis,
        )
        annotated.append({
            "shard_id": shard_id,
            "similarity_score": match["similarity_score"],
            "merge_candidate": match["merge_candidate"],
            "guiding_question": match["guiding_question"],
        })

    # Update manifest
    manifest[file_hash] = {
        "file": path,
        "source_type": source_type,
        "ingested_at": datetime.now().isoformat(),
        "matched_shards": [a["shard_id"] for a in annotated],
        "merge_candidates": [a["shard_id"] for a in annotated if a["merge_candidate"]],
    }
    _save_manifest(manifest)

    return {
        "status": "ingested",
        "file": path,
        "hash": file_hash,
        "source_type": source_type,
        "shards_annotated": len(annotated),
        "merge_candidates": sum(1 for a in annotated if a["merge_candidate"]),
        "matches": annotated,
    }


def _resolve_allowed_ingest_path(file_path: str) -> str:
    """Resolve an input path and enforce root-allowlist boundaries."""
    resolved = Path(file_path).resolve()
    for allowed_root in NIDHOGG_ALLOWED_ROOTS:
        root_path = Path(allowed_root).resolve()
        if resolved.is_relative_to(root_path):
            return str(resolved)
    raise ValueError(f"Access denied for path: {resolved}")


# ═══════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═══════════════════════════════════════════════════════════

def register_nidhogg_tools(mcp) -> None:
    """Register Nidhogg ingestion tools onto an existing FastMCP instance.
    Called once in nova_server.py after server init — same pattern as Gemini.
    """

    @mcp.tool(name="nidhogg_ingest")
    async def nidhogg_ingest(params: NidhoggIngestInput) -> str:
        """
        Ingest a single document into NOVA's shard graph.
        Embeds the file, finds matching shards by cosine similarity, and appends
        a provenance block (nidhogg block) to each matched shard.
        Idempotent — re-ingesting the same file is a no-op.
        """
        if is_blocked("nidhogg_ingest"):
            return denial_payload("nidhogg_ingest")
        result = _ingest_file(params.file_path, params.source_type, params.top_n)
        return json.dumps(result, indent=2)

    @mcp.tool(name="nidhogg_scan")
    async def nidhogg_scan(params: NidhoggScanInput) -> str:
        """
        Scan the intake/ directory and ingest all pending files.
        Skips files already in the manifest (idempotent).
        Supported formats: txt, md, rst, csv, json, yaml, toml, pdf (requires pypdf).
        """
        if is_blocked("nidhogg_scan"):
            return denial_payload("nidhogg_scan")
        os.makedirs(NIDHOGG_INTAKE_DIR, exist_ok=True)
        supported = _TEXT_EXTENSIONS | {".pdf"}

        pending = [
            f for f in Path(NIDHOGG_INTAKE_DIR).iterdir()
            if f.is_file() and f.suffix.lower() in supported
        ]

        if not pending:
            return json.dumps({
                "status": "nothing_to_ingest",
                "intake_dir": NIDHOGG_INTAKE_DIR,
            }, indent=2)

        results = []
        for file_path in sorted(pending):
            result = _ingest_file(str(file_path), params.source_type, params.top_n)
            results.append(result)

        summary = {
            "files_found": len(pending),
            "ingested": sum(1 for r in results if r.get("status") == "ingested"),
            "skipped": sum(1 for r in results if r.get("status") == "skipped"),
            "no_matches": sum(1 for r in results if r.get("status") == "no_matches"),
            "errors": sum(1 for r in results if "error" in r),
            "results": results,
        }
        return json.dumps(summary, indent=2)

    @mcp.tool(name="nidhogg_status")
    async def nidhogg_status(params: NidhoggStatusInput) -> str:
        """
        Show the Nidhogg ingestion manifest — what files have been ingested,
        which shards they matched, and which were flagged as merge candidates.
        """
        if is_blocked("nidhogg_status"):
            return denial_payload("nidhogg_status")
        manifest = _load_manifest()
        if not manifest:
            return json.dumps({"status": "empty", "message": "No files ingested yet."}, indent=2)

        summary = {
            "total_ingested": len(manifest),
            "total_merge_candidates": sum(
                len(v.get("merge_candidates", [])) for v in manifest.values()
            ),
            "entries": [
                {
                    "file": v.get("file", ""),
                    "source_type": v.get("source_type", ""),
                    "ingested_at": v.get("ingested_at", ""),
                    "shards_matched": len(v.get("matched_shards", [])),
                    "merge_candidates": v.get("merge_candidates", []),
                    "note": v.get("note", ""),
                }
                for v in manifest.values()
            ],
        }
        return json.dumps(summary, indent=2)
