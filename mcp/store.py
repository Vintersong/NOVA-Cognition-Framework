"""
store.py — Shard I/O and index management for NOVA.

Owns all filesystem operations for shards and the index.
All path inputs are validated against SHARD_DIR to prevent path traversal.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from filelock import FileLock

from config import SHARD_DIR, INDEX_FILE, MAX_FRAGMENTS


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
    shard_dir_resolved = Path(SHARD_DIR).resolve()
    filepath = (shard_dir_resolved / (shard_id + ".json")).resolve()
    if not filepath.is_relative_to(shard_dir_resolved):
        raise ValueError(f"Invalid shard_id: '{shard_id}' resolves outside shard directory.")
    if not filepath.exists():
        raise FileNotFoundError(f"Shard '{shard_id}' not found.")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f), str(filepath)


def save_shard(filepath: str, data: dict):
    lock_path = filepath + ".lock"
    with FileLock(lock_path, timeout=5):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def update_shard_usage(data: dict):
    meta = data.setdefault("meta_tags", {})
    meta["usage_count"] = meta.get("usage_count", 0) + 1
    meta["last_used"] = datetime.now().isoformat()


def extract_fragments(shard_data: dict, shard_id: str) -> list[str]:
    fragments = []
    for entry in shard_data.get("conversation_history", []):
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
    except Exception:
        return {}


def save_index(index: dict):
    with FileLock(INDEX_FILE + ".lock", timeout=5):
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)


def classify_tags(shard: dict) -> list[str]:
    tags = []
    now = datetime.now()
    meta = shard.get("meta_tags", {})
    usage_count = meta.get("usage_count", 0)
    last_used_str = meta.get("last_used")
    confidence = meta.get("confidence", 1.0)

    if last_used_str:
        try:
            last_used = datetime.fromisoformat(last_used_str)
            if now - last_used < timedelta(days=3):
                tags.append("recent")
            if now - last_used > timedelta(days=14):
                tags.append("stale")
        except (ValueError, TypeError):
            pass

    if usage_count > 10:
        tags.append("frequently_used")
    if meta.get("intent") == "archived":
        tags.append("archived")
    if meta.get("intent") == "forgotten":
        tags.append("forgotten")
    if shard.get("context", {}).get("embedding"):
        tags.append("enriched")
    if confidence < 0.4:
        tags.append("low_confidence")
    if meta.get("last_compacted"):
        tags.append("compacted")

    return tags


def update_index() -> dict:
    index = {}
    if not os.path.exists(SHARD_DIR):
        return index

    for fname in sorted(os.listdir(SHARD_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(SHARD_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                shard = json.load(f)
        except Exception:
            continue

        shard_id = shard.get("shard_id", fname.replace(".json", ""))
        index[shard_id] = {
            "shard_id": shard_id,
            "filename": fname,
            "guiding_question": shard.get("guiding_question", ""),
            "tags": classify_tags(shard),
            "meta": shard.get("meta_tags", {}),
            "context_summary": shard.get("context", {}).get("summary", ""),
            "context_topics": shard.get("context", {}).get("topics", []),
            "confidence": shard.get("meta_tags", {}).get("confidence", 1.0),
        }

    save_index(index)
    return index


def patch_index_entry(shard_id: str, shard_data: dict) -> dict:
    """Update a single shard entry in the index without full rescan."""
    index = load_index()
    index[shard_id] = {
        "shard_id": shard_id,
        "filename": shard_id + ".json",
        "guiding_question": shard_data.get("guiding_question", ""),
        "tags": classify_tags(shard_data),
        "meta": shard_data.get("meta_tags", {}),
        "context_summary": shard_data.get("context", {}).get("summary", ""),
        "context_topics": shard_data.get("context", {}).get("topics", []),
        "confidence": shard_data.get("meta_tags", {}).get("confidence", 1.0),
    }
    save_index(index)
    return index


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
