"""
obsidian_export.py — Export NOVA shards to an Obsidian vault.

One .md file per shard with YAML frontmatter + [[wikilink]] edges from the
knowledge graph. Wiki pages (already Markdown) are copied as-is.

Output dir: NOVA_OBSIDIAN_DIR env var (default: output/obsidian_vault/)

Shard file format:
  ---
  shard_id: <id>
  confidence: 0.87
  theme: general
  intent: reflection
  tags: [recent, enriched]
  valence: 5
  arousal: 5
  provenance: en|WEIRD|LLM
  source: agent_inference
  last_used: 2026-05-11T...
  ---
  # <guiding_question>

  <context_summary>

  ## Topics
  - topic1
  - topic2

  ## Relations
  - extends [[other_shard]]
  - contradicts [[another_shard]]
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parent.parent
OBSIDIAN_DIR = os.environ.get(
    "NOVA_OBSIDIAN_DIR",
    str(_REPO_ROOT / "output" / "obsidian_vault"),
)


def _safe_filename(shard_id: str) -> str:
    """Convert shard_id to a filesystem-safe filename Obsidian accepts."""
    return re.sub(r'[<>:"/\\|?*]', "_", shard_id) + ".md"


def _yaml_str(value: Any) -> str:
    if isinstance(value, str):
        if any(c in value for c in ':#{}[]|>&!'):
            return f'"{value}"'
        return value
    return str(value)


def _build_frontmatter(shard_id: str, entry: dict, shard_data: dict) -> str:
    meta = entry.get("meta", {})
    tags = entry.get("tags", [])
    shard_meta = shard_data.get("meta_tags", {})

    lines = ["---"]
    lines.append(f"shard_id: {_yaml_str(shard_id)}")
    lines.append(f"confidence: {round(entry.get('confidence', 1.0), 4)}")
    lines.append(f"theme: {_yaml_str(meta.get('theme', ''))}")
    lines.append(f"intent: {_yaml_str(meta.get('intent', ''))}")
    lines.append(f"source: {_yaml_str(meta.get('source', 'agent_inference'))}")

    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    else:
        lines.append("tags: []")

    valence = shard_meta.get("valence")
    arousal = shard_meta.get("arousal")
    if valence is not None:
        lines.append(f"valence: {valence}")
    if arousal is not None:
        lines.append(f"arousal: {arousal}")

    provenance = shard_meta.get("provenance", "en|WEIRD|LLM")
    lines.append(f"provenance: {_yaml_str(provenance)}")

    last_used = meta.get("last_used", "")
    if last_used:
        lines.append(f"last_used: {last_used}")

    quarantine = shard_meta.get("quarantine_until")
    if quarantine:
        lines.append(f"quarantine_until: {quarantine}")

    lines.append("---")
    return "\n".join(lines)


def _build_body(shard_id: str, entry: dict, relations: list[dict]) -> str:
    question = entry.get("guiding_question", shard_id)
    summary = entry.get("context_summary", "").strip()
    topics = entry.get("context_topics", [])

    parts = [f"# {question}", ""]

    if summary:
        parts += [summary, ""]

    if topics:
        parts.append("## Topics")
        for t in topics:
            parts.append(f"- {t}")
        parts.append("")

    # Outbound edges from the knowledge graph
    outbound = [r for r in relations if r["source"] == shard_id]
    inbound  = [r for r in relations if r["target"] == shard_id]

    if outbound or inbound:
        parts.append("## Relations")
        for r in outbound:
            note = f" — {r['notes']}" if r.get("notes") else ""
            parts.append(f"- {r['type']} [[{r['target']}]]{note}")
        for r in inbound:
            note = f" — {r['notes']}" if r.get("notes") else ""
            parts.append(f"- ← {r['type']} [[{r['source']}]]{note}")
        parts.append("")

    return "\n".join(parts)


def export_shards(
    index: dict,
    shard_loader: Any,  # callable: shard_id -> (dict, str)
    graph: dict,
    out_dir: str | None = None,
) -> dict:
    """Export all non-archived, non-forgotten shards to Obsidian Markdown files.

    Returns {"exported": N, "skipped": M, "errors": [...]}
    """
    root = Path(out_dir or OBSIDIAN_DIR)
    shards_dir = root / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)

    relations = graph.get("relations", [])
    exported = skipped = 0
    errors: list[str] = []

    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "forgotten" in tags or "archived" in tags:
            skipped += 1
            continue

        try:
            shard_data, _ = shard_loader(shard_id)
        except FileNotFoundError:
            skipped += 1
            continue
        except Exception as exc:
            errors.append(f"{shard_id}: {exc}")
            continue

        frontmatter = _build_frontmatter(shard_id, entry, shard_data)
        body = _build_body(shard_id, entry, relations)
        content = frontmatter + "\n" + body

        out_path = shards_dir / _safe_filename(shard_id)
        try:
            out_path.write_text(content, encoding="utf-8")
            exported += 1
        except OSError as exc:
            errors.append(f"{shard_id}: {exc}")

    _write_index_note(root, index, relations)
    return {"exported": exported, "skipped": skipped, "errors": errors}


def _write_index_note(root: Path, index: dict, relations: list[dict]) -> None:
    """Write a _NOVA_INDEX.md with stats and links to all shards."""
    total = len(index)
    confirmed = sum(1 for e in index.values() if e.get("confidence", 1.0) >= 0.85)
    themes: dict[str, int] = {}
    for e in index.values():
        t = e.get("meta", {}).get("theme", "general")
        themes[t] = themes.get(t, 0) + 1

    lines = [
        "# NOVA Memory Index",
        "",
        f"**Shards:** {total}  |  **High-confidence:** {confirmed}  |  "
        f"**Relations:** {len(relations)}",
        "",
        "## By Theme",
        "",
    ]
    for theme, count in sorted(themes.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"- **{theme}** ({count})")

    lines += ["", "## All Shards", ""]
    for shard_id, entry in sorted(index.items(),
                                   key=lambda x: x[1].get("confidence", 0),
                                   reverse=True):
        tags = entry.get("tags", [])
        if "forgotten" in tags or "archived" in tags:
            continue
        conf = entry.get("confidence", 1.0)
        q = entry.get("guiding_question", shard_id)[:80]
        lines.append(f"- [[shards/{shard_id}|{q}]] `{conf:.2f}`")

    (root / "_NOVA_INDEX.md").write_text("\n".join(lines), encoding="utf-8")
