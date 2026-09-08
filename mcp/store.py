"""
store.py — Shard I/O and index management for NOVA.

Owns all filesystem operations for shards and the index.
All path inputs are validated against SHARD_DIR to prevent path traversal.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock

try:
    import ijson
except ImportError:  # pragma: no cover - optional dependency fallback
    ijson = None

from atomic_io import atomic_write_json, atomic_write_text
from config import (
    SHARD_DIR, INDEX_FILE, SUMMARY_INDEX_FILE, SUMMARY_MARKDOWN_FILE,
    CONFIDENCE_LOW_THRESHOLD, RECENT_ACCESS_DAYS, STALE_ACCESS_DAYS,
)
from timeutils import parse_iso, now_utc

logger = logging.getLogger(__name__)
_error_counts: Counter[str] = Counter()


def _record_error(operation: str, exc: Exception) -> None:
    _error_counts[operation] += 1
    logger.warning("store.%s failed (%s): %s", operation, type(exc).__name__, exc)


# ═══════════════════════════════════════════════════════════
# SHARD I/O
# ═══════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    return name[:40]


def get_unique_filename(base: str) -> str:
    filename = base + ".json"
    i = 1
    while os.path.exists(os.path.join(SHARD_DIR, filename)):
        filename = f"{base}_{i}.json"
        i += 1
    return filename


def load_shard(shard_id: str) -> tuple[dict, str]:
    from shard_format import load_shard_file
    return load_shard_file(shard_id, SHARD_DIR)


def _resolve_write_path(filepath: str) -> str:
    """Resolve the on-disk path a write should target.

    If the shard has already been migrated to .md, writes go to .md even when
    the caller passes the legacy .json path. The returned path also determines
    the lock path (``<path>.lock``), so every writer of a given shard agrees on
    the same mutex.
    """
    from shard_format import md_path_for, is_md_shard
    if not is_md_shard(filepath):
        md = md_path_for(filepath)
        if md.exists():
            return str(md)
    return filepath


def _write_shard_locked(filepath: str, data: dict) -> str:
    """Write the shard body to *filepath*. Caller MUST hold ``filepath + '.lock'``.

    Returns the path actually written (may switch .json → .md). Does not touch
    the Arrow cache or SQLite index — see :func:`_post_write_sync`.
    """
    from shard_format import shard_to_md, is_md_shard
    filepath = _resolve_write_path(filepath)
    if is_md_shard(filepath):
        Path(filepath).write_text(shard_to_md(data), encoding="utf-8")
    else:
        atomic_write_json(filepath, data)
    return filepath


def _post_write_sync(filepath: str, data: dict) -> None:
    """Best-effort secondary-store sync after a shard write. Never raises."""
    # Invalidate the Arrow cache so the next MUNINN rerank / NÓTT pass sees the
    # write. No-op when pyarrow isn't installed (ARROW_AVAILABLE = False).
    try:
        from arrow_cache import ARROW_AVAILABLE, get_arrow_cache
        if ARROW_AVAILABLE:
            get_arrow_cache().invalidate(data.get("shard_id"))
    except Exception as exc:
        _record_error("save_shard_invalidate_arrow", exc)

    # Keep SQLite secondary index in sync — best-effort, never blocks the write.
    try:
        from nova_shard_db import get_nova_shard_db
        mtime_ns = int(Path(filepath).stat().st_mtime_ns) if os.path.exists(filepath) else 0
        get_nova_shard_db().upsert_from_shard(data, mtime_ns)
    except Exception as exc:
        _record_error("save_shard_sync_sqlite", exc)


def save_shard(filepath: str, data: dict):
    filepath = _resolve_write_path(filepath)
    with FileLock(filepath + ".lock", timeout=5):
        written = _write_shard_locked(filepath, data)
    _post_write_sync(written, data)


def shard_revision(data: dict) -> str:
    """Cheap change-signature for optimistic concurrency control.

    Two reads of the same shard return the same revision iff no field changed
    in between. Used by :func:`mutate_shard` (``expect_revision``) so a body
    rewrite (e.g. NÓTT compaction) can be skipped when the shard was modified
    out from under the computation that produced the new body.
    """
    import hashlib
    blob = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _apply_field_diff(fresh: dict, before: dict, after: dict) -> None:
    """Overlay the (before → after) delta onto *fresh*, one level deep.

    Only keys the caller actually changed are written, so fields a concurrent
    writer touched on *fresh* (notably the conversation body) are preserved.
    Nested dicts (e.g. ``meta_tags``) are merged per sub-key rather than wholesale
    so a concurrent ``meta_tags`` change isn't clobbered either.
    """
    for key in set(before) | set(after):
        bv, av = before.get(key), after.get(key)
        if bv == av:
            continue
        if isinstance(bv, dict) and isinstance(av, dict) and isinstance(fresh.get(key), dict):
            for sk in set(bv) | set(av):
                if bv.get(sk) == av.get(sk):
                    continue
                if sk in av:
                    fresh[key][sk] = av[sk]
                else:
                    fresh[key].pop(sk, None)
        elif key in after:
            fresh[key] = av
        else:
            fresh.pop(key, None)


def mutate_shard(shard_id: str, mutator, *, expect_revision: str | None = None) -> bool:
    """Atomically load → mutate → write a shard under its FileLock.

    The authoritative read happens *inside* the lock, so a concurrent writer's
    changes to fields *mutator* does not touch are preserved. This closes the
    NÓTT ↔ tool-write lost-update race where a stale whole-object write would
    silently drop a freshly-appended conversation turn.

    *mutator* receives the fresh shard dict and mutates it in place.
    *expect_revision* (from :func:`shard_revision`) makes the write conditional:
    if the shard changed since the revision was taken, the write is skipped and
    ``False`` is returned. Use it for body-rewriting passes (compaction) whose
    new body was computed outside the lock.

    Returns ``True`` if written, ``False`` if the shard was missing or the
    revision guard failed.
    """
    try:
        _, filepath = load_shard(shard_id)
    except FileNotFoundError:
        return False
    filepath = _resolve_write_path(filepath)
    with FileLock(filepath + ".lock", timeout=5):
        try:
            data, filepath = load_shard(shard_id)
        except FileNotFoundError:
            return False
        if expect_revision is not None and shard_revision(data) != expect_revision:
            return False
        mutator(data)
        written = _write_shard_locked(filepath, data)
    _post_write_sync(written, data)
    return True


def mutate_shard_fields(shard_id: str, before: dict, after: dict) -> bool:
    """Atomically apply the (before → after) field delta to a shard.

    Convenience wrapper over :func:`mutate_shard` for the common case where a
    caller loaded a snapshot, mutated some metadata fields, and wants only those
    fields written onto the current on-disk shard. Returns ``True`` if written.
    """
    return mutate_shard(shard_id, lambda fresh: _apply_field_diff(fresh, before, after))


def update_shard_usage(data: dict):
    meta = data.setdefault("meta_tags", {})
    meta["usage_count"] = meta.get("usage_count", 0) + 1
    meta["last_used"] = datetime.now().isoformat()


def extract_fragments(shard_data: dict, shard_id: str, max_turns: int | None = None) -> list[str]:
    """Render the shard's conversation history into [SHARD: …] User/NOVA lines.

    `max_turns` slices the *last N turns* from conversation_history before
    rendering. Each turn produces up to two lines (user + ai), so the previous
    pattern of slicing the rendered list `[-MAX_FRAGMENTS:]` produced half as
    many turns as the variable name implied.
    """
    history = shard_data.get("conversation_history", [])
    if max_turns is not None and max_turns >= 0:
        history = history[-max_turns:]
    fragments = []
    for entry in history:
        if entry.get("user"):
            fragments.append(f"[SHARD: {shard_id}] User: {entry['user']}")
        if entry.get("ai"):
            fragments.append(f"[SHARD: {shard_id}] NOVA: {entry['ai']}")
    return fragments


# ═══════════════════════════════════════════════════════════
# INDEX MANAGEMENT
# ═══════════════════════════════════════════════════════════

def load_index() -> dict:
    if not os.path.exists(INDEX_FILE):
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        _record_error("load_index", exc)
        return {}


def save_index(index: dict):
    with FileLock(INDEX_FILE + ".lock", timeout=5):
        atomic_write_json(INDEX_FILE, index)


def classify_tags(shard: dict) -> list[str]:
    tags = []
    now = now_utc()
    meta = shard.get("meta_tags", {})
    usage_count = meta.get("usage_count", 0)
    last_used_str = meta.get("last_used")
    confidence = meta.get("confidence", 1.0)

    last_used = parse_iso(last_used_str) if last_used_str else None
    if last_used is not None:
        if now - last_used < timedelta(days=RECENT_ACCESS_DAYS):
            tags.append("recent")
        if now - last_used > timedelta(days=STALE_ACCESS_DAYS):
            tags.append("stale")
    elif last_used_str:
        _error_counts["classify_tags"] += 1
        logger.warning("store.classify_tags ignored invalid last_used value: %r", last_used_str)

    if usage_count > 10:
        tags.append("frequently_used")
    if meta.get("intent") == "archived":
        tags.append("archived")
    if meta.get("intent") == "forgotten":
        tags.append("forgotten")
    if shard.get("context", {}).get("embedding"):
        tags.append("enriched")
    if confidence < CONFIDENCE_LOW_THRESHOLD:
        tags.append("low_confidence")
    if meta.get("last_compacted"):
        tags.append("compacted")

    return tags


def update_index() -> dict:
    index = {}
    if not os.path.exists(SHARD_DIR):
        return index

    from shard_format import load_shard_file

    # Collect all shard IDs from both .md and .json files.
    # When both exist for the same ID, prefer .md (it's the migrated format).
    seen: dict[str, str] = {}  # shard_id -> filename
    for fname in sorted(os.listdir(SHARD_DIR)):
        if fname.endswith(".md"):
            sid = fname[:-3]
            seen[sid] = fname  # .md wins unconditionally
        elif fname.endswith(".json") and not fname.endswith(".lock"):
            sid = fname[:-5]
            if sid not in seen:
                seen[sid] = fname

    for sid, fname in seen.items():
        try:
            shard, _ = load_shard_file(sid, SHARD_DIR)
        except Exception as exc:
            _record_error("update_index", exc)
            continue

        shard_id = shard.get("shard_id", sid)
        context_summary = shard.get("context", {}).get("summary", "")
        # Mirror context.summary into meta.summary so adversarial.py and recall.py
        # filters that read entry["meta"]["summary"] aren't silent no-ops.
        meta = dict(shard.get("meta_tags", {}))
        if context_summary and not meta.get("summary"):
            meta["summary"] = context_summary
        index[shard_id] = {
            "shard_id": shard_id,
            "filename": fname,
            "guiding_question": shard.get("guiding_question", ""),
            "tags": classify_tags(shard),
            "meta": meta,
            "context_summary": context_summary,
            "context_topics": shard.get("context", {}).get("topics", []),
            "confidence": shard.get("meta_tags", {}).get("confidence", 1.0),
        }

    save_index(index)
    return index


def patch_index_entry(shard_id: str, shard_data: dict) -> dict:
    """Update a single shard entry in the index without full rescan."""
    index = load_index()
    context_summary = shard_data.get("context", {}).get("summary", "")
    meta = dict(shard_data.get("meta_tags", {}))
    if context_summary and not meta.get("summary"):
        meta["summary"] = context_summary
    index[shard_id] = {
        "shard_id": shard_id,
        "filename": shard_id + ".json",
        "guiding_question": shard_data.get("guiding_question", ""),
        "tags": classify_tags(shard_data),
        "meta": meta,
        "context_summary": context_summary,
        "context_topics": shard_data.get("context", {}).get("topics", []),
        "confidence": shard_data.get("meta_tags", {}).get("confidence", 1.0),
    }
    save_index(index)
    return index


def passes_state_gate(entry: dict, project_context: str = "") -> bool:
    """
    Return False for shards that should be excluded from retrieval based on
    state-aware preconditions:
      - superseded_by is set (another shard has replaced this one)
      - validity_window.end is in the past
      - validity_window.start is in the future
      - project_context is set on both shard and env, and they don't match

    Absence of any precondition field means no restriction.
    """
    meta = entry.get("meta", {})
    now = now_utc()

    if meta.get("superseded_by"):
        return False

    validity = meta.get("validity_window")
    if validity:
        start = parse_iso(validity.get("start"))
        if start is not None and start > now:
            return False
        end = parse_iso(validity.get("end"))
        if end is not None and end < now:
            return False

    shard_ctx = meta.get("project_context")
    if shard_ctx and project_context:
        if isinstance(shard_ctx, list):
            if project_context not in shard_ctx:
                return False
        elif shard_ctx != project_context:
            return False

    return True


def _format_created_date(raw: Any, fallback_path: Path | None = None) -> str:
    if isinstance(raw, str) and raw:
        candidate = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(candidate).date().isoformat()
        except ValueError:
            return raw[:10]
    if fallback_path is not None and fallback_path.exists():
        return datetime.fromtimestamp(fallback_path.stat().st_mtime).date().isoformat()
    return ""


def _coerce_tags(theme: str, tags: list[str], intent: str = "") -> list[str]:
    ordered: list[str] = []
    for value in [theme, *tags, intent]:
        if not value:
            continue
        normalized = str(value).strip().lower().replace(" ", "_")
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _truncate_text(text: str, limit: int) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 1)].rstrip() + "..."


def _extract_synopsis_source(data: dict) -> str:
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    return data.get("summary") or context.get("summary", "") or data.get("guiding_question", "")


def _build_skeleton_from_full_data(data: dict, fallback_path: Path | None = None) -> dict:
    meta = data.get("meta_tags") if isinstance(data.get("meta_tags"), dict) else {}
    turns = data.get("conversation_history")
    if not isinstance(turns, list):
        turns = data.get("turns") if isinstance(data.get("turns"), list) else []

    theme = data.get("theme") or meta.get("theme", "")
    intent = data.get("intent") or meta.get("intent", "")
    raw_tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    return {
        "id": data.get("shard_id") or data.get("id") or (fallback_path.stem if fallback_path else ""),
        "guiding_question": data.get("guiding_question", ""),
        "theme": theme,
        "intent": intent,
        "tags": _coerce_tags(theme, [str(tag) for tag in raw_tags], intent),
        "confidence": float(data.get("confidence", meta.get("confidence", 1.0)) or 1.0),
        "created": _format_created_date(data.get("created_at") or meta.get("created") or meta.get("last_used"), fallback_path),
        "turn_count": len(turns),
        "synopsis_source": _extract_synopsis_source(data),
    }


def read_shard_skeleton(shard_path: str | Path) -> dict:
    path = Path(shard_path)
    if ijson is None:
        with open(path, "r", encoding="utf-8") as handle:
            return _build_skeleton_from_full_data(json.load(handle), path)

    skeleton = {
        "id": path.stem,
        "guiding_question": "",
        "theme": "",
        "intent": "",
        "tags": [],
        "confidence": 1.0,
        "created": "",
        "turn_count": 0,
        "synopsis_source": "",
    }
    raw_tags: list[str] = []

    with open(path, "rb") as handle:
        for prefix, event, value in ijson.parse(handle):
            if prefix == "shard_id" and event == "string":
                skeleton["id"] = value
            elif prefix == "guiding_question" and event == "string":
                skeleton["guiding_question"] = value
            elif prefix == "theme" and event == "string":
                skeleton["theme"] = value
            elif prefix == "intent" and event == "string":
                skeleton["intent"] = value
            elif prefix == "confidence" and event in {"number", "double", "integer"}:
                skeleton["confidence"] = float(value)
            elif prefix == "created_at" and event == "string":
                skeleton["created"] = _format_created_date(value, path)
            elif prefix == "summary" and event == "string" and not skeleton["synopsis_source"]:
                skeleton["synopsis_source"] = value
            elif prefix == "tags.item" and event == "string":
                raw_tags.append(value)
            elif prefix == "meta_tags.theme" and event == "string" and not skeleton["theme"]:
                skeleton["theme"] = value
            elif prefix == "meta_tags.intent" and event == "string" and not skeleton["intent"]:
                skeleton["intent"] = value
            elif prefix == "meta_tags.confidence" and event in {"number", "double", "integer"}:
                skeleton["confidence"] = float(value)
            elif prefix == "meta_tags.created" and event == "string" and not skeleton["created"]:
                skeleton["created"] = _format_created_date(value, path)
            elif prefix == "meta_tags.last_used" and event == "string" and not skeleton["created"]:
                skeleton["created"] = _format_created_date(value, path)
            elif prefix == "context.summary" and event == "string" and not skeleton["synopsis_source"]:
                skeleton["synopsis_source"] = value
            elif prefix in {"conversation_history.item", "turns.item"} and event == "start_map":
                skeleton["turn_count"] += 1

    skeleton["created"] = skeleton["created"] or _format_created_date("", path)
    skeleton["tags"] = _coerce_tags(skeleton["theme"], raw_tags, skeleton["intent"])
    return skeleton


def read_md_shard_skeleton(path: Path) -> dict:
    """Skeleton for a ``.md`` shard, read from its YAML frontmatter.

    The Markdown format keeps everything the browse row needs in the
    frontmatter, so this stops at the closing fence rather than parsing the
    conversation body — the same reason ``read_shard_skeleton`` streams the
    JSON form with ijson instead of json.load. The turn count is the one field
    that does need the body, and counting its ``---`` separators is far cheaper
    than parsing the turns.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    fm: dict = {}
    body = ""
    if len(parts) >= 3:
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as exc:
            _record_error("read_md_shard_skeleton", exc)
            fm = {}
        body = parts[2]

    theme = fm.get("theme", "") or ""
    intent = fm.get("intent", "") or ""
    skeleton = {
        "id": fm.get("shard_id") or path.stem,
        "guiding_question": fm.get("guiding_question", "") or "",
        "theme": theme,
        "intent": intent,
        "confidence": float(fm.get("confidence", 1.0) or 1.0),
        "created": _format_created_date(
            fm.get("created") or fm.get("last_used") or "", path,
        ),
        # Separator count, not turn parsing: a trailing separator after the last
        # turn would over-count, so count non-empty blocks instead.
        "turn_count": sum(1 for b in re.split(r"\n---\n", body) if b.strip()),
        "synopsis_source": fm.get("context_summary", "") or "",
    }
    skeleton["tags"] = _coerce_tags(theme, fm.get("tags") or [], intent)
    return skeleton


def iter_shard_skeletons() -> list[dict]:
    """Skeletons for every shard on disk, in both storage formats.

    This used to glob ``*.json`` only, while ``load_shard_file`` prefers ``.md``
    — so a shard migrated to Markdown was readable by id but invisible to
    nova_shard_index, nova_shard_summary and nova_shard_list, which all sit on
    this function.
    """
    shard_dir = Path(SHARD_DIR)
    if not shard_dir.exists():
        return []
    readers = {".json": read_shard_skeleton, ".md": read_md_shard_skeleton}
    rows = []
    for path in sorted(shard_dir.iterdir()):
        # atomic_io writes ".tmp_*" siblings in this directory and renames them
        # into place; catching one mid-write is a guaranteed FileNotFoundError.
        if path.name.startswith("."):
            continue
        reader = readers.get(path.suffix.lower())
        if reader is None:
            continue
        try:
            rows.append(reader(path))
        except Exception as exc:
            _record_error("iter_shard_skeletons", exc)
            continue
    return rows


def load_summary_index() -> dict:
    if not os.path.exists(SUMMARY_INDEX_FILE):
        return {"_v": 1, "shards": {}}
    try:
        with open(SUMMARY_INDEX_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        _record_error("load_summary_index", exc)
        return {"_v": 1, "shards": {}}
    if isinstance(data, dict) and "shards" in data:
        return data
    if isinstance(data, dict):
        return {"_v": 1, "shards": data}
    return {"_v": 1, "shards": {}}


def save_summary_index(summary_index: dict):
    payload = {
        "_v": 1,
        "updated_at": datetime.now().isoformat(),
        "shards": summary_index.get("shards", {}),
    }
    with FileLock(SUMMARY_INDEX_FILE + ".lock", timeout=5):
        atomic_write_json(SUMMARY_INDEX_FILE, payload)


def heuristic_summary_sentence(skeleton: dict) -> str:
    source = skeleton.get("synopsis_source") or skeleton.get("guiding_question") or skeleton.get("id", "")
    return _truncate_text(source, 80)


def heuristic_synopsis(skeleton: dict) -> str:
    source = skeleton.get("synopsis_source") or skeleton.get("guiding_question") or skeleton.get("id", "")
    return _truncate_text(source, 240)


def build_browse_row(skeleton: dict, summary_entry: dict | None = None, include_synopsis: bool = False) -> dict:
    summary_entry = summary_entry or {}
    row = {
        "id": skeleton.get("id", ""),
        "d": summary_entry.get("d") or heuristic_summary_sentence(skeleton),
        "t": skeleton.get("tags", []),
        "c": round(float(skeleton.get("confidence", 1.0) or 1.0), 3),
        "created": skeleton.get("created", ""),
        "n": int(skeleton.get("turn_count", 0) or 0),
    }
    if include_synopsis:
        row["s"] = summary_entry.get("s") or heuristic_synopsis(skeleton)
    return row


def collect_browse_rows(include_synopsis: bool = False) -> list[dict]:
    summary_index = load_summary_index().get("shards", {})
    return [
        build_browse_row(skeleton, summary_index.get(skeleton["id"]), include_synopsis=include_synopsis)
        for skeleton in iter_shard_skeletons()
    ]


def filter_sort_paginate_rows(
    rows: list[dict],
    filter_tag: str = "",
    min_confidence: float | None = None,
    sort: str = "confidence",
    sort_order: str = "desc",
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict], int]:
    filtered = []
    normalized_tag = filter_tag.strip().lower()
    for row in rows:
        if normalized_tag and normalized_tag not in row.get("t", []):
            continue
        if min_confidence is not None and row.get("c", 0.0) < min_confidence:
            continue
        filtered.append(row)

    sort_key_map = {
        "confidence": lambda item: item.get("c", 0.0),
        "created": lambda item: item.get("created", ""),
        "turn_count": lambda item: item.get("n", 0),
        "id": lambda item: item.get("id", ""),
    }
    reverse = sort_order != "asc"
    filtered.sort(key=sort_key_map.get(sort, sort_key_map["confidence"]), reverse=reverse)

    total = len(filtered)
    start = max(page - 1, 0) * per_page
    end = start + per_page
    return filtered[start:end], total


def group_rows_by_theme(rows: list[dict]) -> dict:
    themes: dict[str, dict[str, Any]] = {}
    for row in rows:
        tags = row.get("t", [])
        theme = tags[0] if tags else "unclassified"
        bucket = themes.setdefault(theme, {"count": 0, "shards": []})
        bucket["count"] += 1
        bucket["shards"].append(row)
    return themes


def save_summary_markdown(rows: list[dict]):
    lines = []
    for row in rows:
        tags = row.get("t", [])
        theme = tags[0] if tags else "unclassified"
        hashtags = " ".join(f"#{tag}" for tag in tags)
        lines.append(f"- [{theme}] {row.get('d', '')} | conf:{row.get('c', 0):.2f} | {hashtags}".rstrip())
    with FileLock(SUMMARY_MARKDOWN_FILE + ".lock", timeout=5):
        atomic_write_text(SUMMARY_MARKDOWN_FILE, "\n".join(lines) + ("\n" if lines else ""))


def rebuild_summary_markdown_from_store():
    save_summary_markdown(collect_browse_rows(include_synopsis=False))


def generate_haiku_summary_batch(shards: list[dict], batch_size: int = 5) -> dict[str, str]:
    if not shards:
        return {}

    from config import CLAUDE_API_KEY, HUGINN_MODEL

    if not CLAUDE_API_KEY:
        return {}

    import anthropic
    import httpx

    _SUMMARY_API_TIMEOUT = float(os.environ.get("NOVA_SUMMARY_API_TIMEOUT", "12"))

    sample = list(shards)
    random.shuffle(sample)
    batch = sample[:batch_size]
    prompt = (
        "For each shard below, return a JSON array where each item has "
        "shard_id and summary (one sentence, max 80 chars, plain text). "
        "Return only JSON, no preamble.\n\n"
        f"Shards:\n{json.dumps(batch, indent=2)}"
    )

    try:
        client = anthropic.Anthropic(
            api_key=CLAUDE_API_KEY,
            timeout=httpx.Timeout(_SUMMARY_API_TIMEOUT, connect=5.0),
        )
        response = client.messages.create(
            model=HUGINN_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("generate_haiku_summary_batch failed: %s", exc)
        return {}

    return {
        item["shard_id"]: _truncate_text(item.get("summary", ""), 80)
        for item in parsed
        if isinstance(item, dict) and item.get("shard_id")
    }


def refresh_summary_index_entry(shard_id: str, shard_data: dict, generate_missing: bool = True):
    skeleton = _build_skeleton_from_full_data(shard_data)
    summary_index = load_summary_index()
    entry = summary_index.setdefault("shards", {}).get(shard_id, {})
    synopsis = heuristic_synopsis(skeleton)
    summary_sentence = entry.get("d") or heuristic_summary_sentence(skeleton)

    if generate_missing and not entry.get("d"):
        generated = generate_haiku_summary_batch([
            {
                "shard_id": shard_id,
                "guiding_question": skeleton.get("guiding_question", ""),
                "theme": skeleton.get("theme", ""),
                "tags": skeleton.get("tags", []),
                "synopsis": synopsis,
            }
        ], batch_size=1)
        summary_sentence = generated.get(shard_id, summary_sentence)

    summary_index["shards"][shard_id] = {
        "d": _truncate_text(summary_sentence, 80),
        "s": synopsis,
        "updated_at": datetime.now().isoformat(),
    }
    save_summary_index(summary_index)
    rebuild_summary_markdown_from_store()


def rebuild_summary_indexes(generate_missing: bool = False, batch_size: int = 5) -> dict:
    summary_index = load_summary_index()
    rows_for_generation = []
    for skeleton in iter_shard_skeletons():
        shard_id = skeleton["id"]
        entry = summary_index.setdefault("shards", {}).get(shard_id, {})
        synopsis = heuristic_synopsis(skeleton)
        if not entry.get("s"):
            entry["s"] = synopsis
        if not entry.get("d"):
            entry["d"] = heuristic_summary_sentence(skeleton)
            rows_for_generation.append(
                {
                    "shard_id": shard_id,
                    "guiding_question": skeleton.get("guiding_question", ""),
                    "theme": (skeleton.get("tags") or [""])[0],
                    "tags": skeleton.get("tags", []),
                    "synopsis": synopsis,
                }
            )
        entry["updated_at"] = datetime.now().isoformat()
        summary_index["shards"][shard_id] = entry

    if generate_missing and rows_for_generation:
        generated = generate_haiku_summary_batch(rows_for_generation, batch_size=batch_size)
        for shard_id, summary_sentence in generated.items():
            summary_index["shards"].setdefault(shard_id, {})["d"] = summary_sentence

    save_summary_index(summary_index)
    rebuild_summary_markdown_from_store()
    return summary_index


# ═══════════════════════════════════════════════════════════
# LEGACY RETRIEVAL FALLBACK
# ═══════════════════════════════════════════════════════════

def guess_relevant_shards(message: str, index: dict, top_n: int = 3) -> list[str]:
    """
    Fuzzy match with confidence weighting.
    Legacy fallback — prefer _huginn.retrieve() at call sites.
    """
    scored = []
    msg_lower = message.lower()
    msg_tokens = set(msg_lower.split())

    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "archived" in tags or "forgotten" in tags:
            continue

        confidence = entry.get("confidence", 1.0)

        searchable = " ".join([
            entry.get("guiding_question", ""),
            entry.get("context_summary", ""),
            " ".join(entry.get("context_topics", [])),
            entry.get("meta", {}).get("theme", ""),
            entry.get("meta", {}).get("intent", ""),
        ]).lower()

        search_tokens = set(searchable.split())
        overlap = msg_tokens & search_tokens
        base_score = len(overlap) / max(len(msg_tokens), 1)
        weighted_score = base_score * confidence  # inline confidence_weighted_score

        if weighted_score > 0.05:
            scored.append((shard_id, weighted_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:top_n]]
