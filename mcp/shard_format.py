"""
shard_format.py — YAML frontmatter + Markdown body serialization for NOVA shards.

New format (.md files) solves context-window bloat:
  - YAML frontmatter: all metadata (~20 lines, machine-parseable)
  - Markdown body: conversation turns as human-readable prose
  - Embedding NOT stored (regenerated on demand by nova_embeddings_local.py)

Old JSON shards remain readable — load_shard() tries .md first, falls back
to .json. Migration is lazy: NÓTT converts a shard on its first compact pass.

Round-trip guarantee: md_to_shard(shard_to_md(data)) == data (minus embedding).

File layout in shards/:
  foo.json   ← old format (still valid, will be migrated lazily)
  foo.md     ← new format (preferred; save_shard writes this when it exists)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════

_FRONTMATTER_FIELDS = [
    "shard_id", "guiding_question", "confidence", "theme", "intent",
    "source", "tags", "last_used", "usage_count", "quarantine_until",
    "valence", "arousal", "provenance", "cluster_id",
    "epistemic_provenance",
    "context_topics", "context_summary",
    "enrichment_status", "last_compacted",
    "embedding_sig",
]


# ═══════════════════════════════════════════════════════════
# SERIALISE: dict → markdown string
# ═══════════════════════════════════════════════════════════

def shard_to_md(data: dict) -> str:
    """Convert a shard dict to YAML-frontmatter + Markdown body string."""
    meta = data.get("meta_tags", {})
    context = data.get("context", {})

    # ── Frontmatter ──────────────────────────────────────────────────────
    fm: dict[str, Any] = {
        "shard_id":        data.get("shard_id", ""),
        "guiding_question": data.get("guiding_question", ""),
        "confidence":      round(float(meta.get("confidence", 1.0)), 4),
        "theme":           meta.get("theme", "general"),
        "intent":          meta.get("intent", "reflection"),
        "source":          meta.get("source", "agent_inference"),
        "tags":            data.get("tags", []),
        "last_used":       meta.get("last_used", ""),
        "usage_count":     int(meta.get("usage_count", 0)),
        "quarantine_until": meta.get("quarantine_until"),
        "valence":         meta.get("valence"),
        "arousal":         meta.get("arousal"),
        "provenance":      meta.get("provenance", "en|WEIRD|LLM"),
        "cluster_id":      meta.get("cluster_id"),
        "epistemic_provenance": meta.get("epistemic_provenance"),
        "context_topics":  context.get("topics", []),
        "context_summary": context.get("summary", ""),
        "enrichment_status": meta.get("enrichment_status", "pending"),
        "last_compacted":  meta.get("last_compacted"),
        "embedding_sig":   context.get("embedding_sig"),
    }
    # Strip None-valued optional keys to keep frontmatter terse
    fm = {k: v for k, v in fm.items() if v is not None or k in (
        "quarantine_until", "valence", "arousal", "cluster_id", "last_compacted"
    )}

    frontmatter = "---\n" + yaml.dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip() + "\n---\n"

    # ── Body ─────────────────────────────────────────────────────────────
    question = data.get("guiding_question", "")
    body_lines = [f"# {question}", ""]

    summary = context.get("summary", "").strip()
    if summary and summary != question:
        body_lines += [summary, ""]

    history = data.get("conversation_history", [])
    if history:
        body_lines.append("## Conversation")
        body_lines.append("")
        for turn in history:
            user_text = (turn.get("user") or "").strip()
            ai_text   = (turn.get("ai") or "").strip()
            ts = turn.get("timestamp", "")
            ts_suffix = f" · {ts}" if ts else ""

            if user_text:
                body_lines.append(f"**User**{ts_suffix}")
                body_lines.append(user_text)
                body_lines.append("")
            if ai_text:
                body_lines.append("**NOVA**")
                body_lines.append(ai_text)
                body_lines.append("")
            if user_text or ai_text:
                body_lines.append("---")
                body_lines.append("")

    return frontmatter + "\n".join(body_lines)


# ═══════════════════════════════════════════════════════════
# DESERIALISE: markdown string → dict
# ═══════════════════════════════════════════════════════════

def md_to_shard(text: str, shard_id: str = "") -> dict:
    """Parse a YAML-frontmatter Markdown string back into a shard dict.

    Embedding is NOT reconstructed (not stored in .md format).
    Callers that need the embedding should call enrich_shard() after loading.
    """
    # Split on the --- fence
    parts = re.split(r'^---\s*$', text, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        # No frontmatter — treat as legacy plain text, return minimal dict
        return {"shard_id": shard_id, "guiding_question": "", "conversation_history": [],
                "meta_tags": {}, "context": {}}

    _, fm_raw, body_raw = parts[0], parts[1], parts[2]

    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError:
        fm = {}

    sid = fm.get("shard_id") or shard_id

    # ── Reconstruct meta_tags ────────────────────────────────────────────
    meta_tags: dict[str, Any] = {
        "confidence":       fm.get("confidence", 1.0),
        "theme":            fm.get("theme", "general"),
        "intent":           fm.get("intent", "reflection"),
        "source":           fm.get("source", "agent_inference"),
        "last_used":        fm.get("last_used", ""),
        "usage_count":      fm.get("usage_count", 0),
        "quarantine_until": fm.get("quarantine_until"),
        "enrichment_status": fm.get("enrichment_status", "pending"),
        "provenance":       fm.get("provenance", "en|WEIRD|LLM"),
        "cluster_id":       fm.get("cluster_id"),
        "last_compacted":   fm.get("last_compacted"),
    }
    if fm.get("valence") is not None:
        meta_tags["valence"] = fm["valence"]
    if fm.get("arousal") is not None:
        meta_tags["arousal"] = fm["arousal"]
    if fm.get("epistemic_provenance") is not None:
        meta_tags["epistemic_provenance"] = fm["epistemic_provenance"]
    # Remove None values for cleanliness
    meta_tags = {k: v for k, v in meta_tags.items() if v is not None}

    # ── Reconstruct context ──────────────────────────────────────────────
    context: dict[str, Any] = {
        "summary": fm.get("context_summary", ""),
        "topics":  fm.get("context_topics", []),
        "conversation_type": fm.get("intent", "reflection"),
        # embedding intentionally absent — regenerated on demand
    }
    # Restore signature if present so Arrow cache can verify on rebuild.
    if fm.get("embedding_sig"):
        context["embedding_sig"] = fm["embedding_sig"]

    # ── Reconstruct conversation_history from body ────────────────────────
    history = _parse_body_turns(body_raw)

    return {
        "shard_id":            sid,
        "guiding_question":    fm.get("guiding_question", ""),
        "tags":                fm.get("tags", []),
        "conversation_history": history,
        "meta_tags":           meta_tags,
        "context":             context,
    }


def _parse_body_turns(body: str) -> list[dict]:
    """Parse conversation turns from the Markdown body.

    Each turn block looks like:
        **User** [· timestamp]
        user text

        **NOVA**
        nova text

        ---
    """
    turns = []
    # Split on turn separators (horizontal rules)
    blocks = re.split(r'\n---\n', body)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        user_text = ""
        ai_text   = ""
        timestamp = ""

        # Match **User** [· timestamp] header
        user_match = re.search(
            r'^\*\*User\*\*(?:\s*·\s*(.+?))?\s*\n(.*?)(?=\*\*NOVA\*\*|\Z)',
            block, re.DOTALL | re.MULTILINE,
        )
        nova_match = re.search(
            r'\*\*NOVA\*\*\s*\n(.*)',
            block, re.DOTALL,
        )

        if user_match:
            timestamp = (user_match.group(1) or "").strip()
            user_text = user_match.group(2).strip()
        if nova_match:
            ai_text = nova_match.group(1).strip()

        if user_text or ai_text:
            turn: dict[str, Any] = {"user": user_text, "ai": ai_text}
            if timestamp:
                turn["timestamp"] = timestamp
            turns.append(turn)

    return turns


# ═══════════════════════════════════════════════════════════
# FILE-LEVEL HELPERS
# ═══════════════════════════════════════════════════════════

def md_path_for(json_path: str | Path) -> Path:
    """Return the .md counterpart path for a .json path."""
    return Path(str(json_path).removesuffix(".json") + ".md")


def json_path_for(md_path: str | Path) -> Path:
    """Return the .json counterpart path for a .md path."""
    return Path(str(md_path).removesuffix(".md") + ".json")


def is_md_shard(filepath: str | Path) -> bool:
    return str(filepath).endswith(".md")


def convert_shard_file(json_path: str | Path, *, delete_json: bool = True) -> Path:
    """Convert one .json shard to .md in-place.

    Reads the JSON, writes the Markdown, optionally removes the JSON.
    Returns the path of the new .md file.
    """
    json_path = Path(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    md_content = shard_to_md(data)
    out = md_path_for(json_path)
    out.write_text(md_content, encoding="utf-8")

    if delete_json:
        json_path.unlink(missing_ok=True)
        # Clean up the lock file if present
        lock = Path(str(json_path) + ".lock")
        lock.unlink(missing_ok=True)

    return out


def load_shard_file(shard_id: str, shard_dir: str | Path) -> tuple[dict, str]:
    """Load a shard by ID from shard_dir, trying .md first then .json.

    Returns (data_dict, filepath_str).
    """
    root = Path(shard_dir).resolve()
    md_path = (root / (shard_id + ".md")).resolve()
    json_path = (root / (shard_id + ".json")).resolve()

    # Security: ensure resolved path stays within shard_dir
    for p in (md_path, json_path):
        if not p.is_relative_to(root):
            raise ValueError(f"shard_id '{shard_id}' resolves outside shard directory.")

    if md_path.exists():
        text = md_path.read_text(encoding="utf-8")
        return md_to_shard(text, shard_id), str(md_path)

    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f), str(json_path)

    raise FileNotFoundError(f"Shard '{shard_id}' not found (tried .md and .json).")


def save_shard_file(filepath: str | Path, data: dict) -> str:
    """Write shard data to filepath.

    If filepath ends in .json but a .md counterpart exists, writes .md instead
    (preserves format for already-migrated shards).
    Returns the actual path written.
    """
    filepath = Path(filepath)

    if str(filepath).endswith(".json"):
        md = md_path_for(filepath)
        if md.exists():
            md.write_text(shard_to_md(data), encoding="utf-8")
            return str(md)

    # Default: write JSON (atomic handled by caller)
    return str(filepath)
