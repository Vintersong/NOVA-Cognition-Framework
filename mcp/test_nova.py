"""
test_nova.py — NOVA Memory Explorer

Reads from your real shard store and builds a live picture of what's in memory:
  - Theme distribution across all shards
  - Confidence health (high / at-risk / low)
  - Top conversations per theme with actual excerpt
  - Cross-theme search to find related threads
  - Oldest untouched shards (decay candidates)

Usage:
    cd mcp
    python test_nova.py
"""
import asyncio
import json
import sys
from collections import defaultdict, Counter

import nova_server
from nova_server import (
    nova_shard_index,
    nova_shard_search,
    nova_shard_get,
)
from schemas import ShardIndexInput, ShardSearchInput, ShardGetInput

W  = 72  # output width


def bar(value: float, width: int = 20) -> str:
    filled = int(round(value * width))
    return "█" * filled + "░" * (width - filled)


def excerpt(shard_get_result: dict, max_chars: int = 200) -> str:
    """Pull the first meaningful user-authored fragment from a raw shard."""
    for turn in shard_get_result.get("conversation_history", []):
        text = str(turn.get("user", "")).strip()
        if len(text) > 20:
            return (text[:max_chars] + "…") if len(text) > max_chars else text
    for turn in shard_get_result.get("turns", []):
        if turn.get("role") == "user":
            text = str(turn.get("content", "")).strip()
            if len(text) > 20:
                return (text[:max_chars] + "…") if len(text) > max_chars else text
    return "(no fragments)"


def confidence_band(c: float) -> str:
    if c >= 0.75:  return "▲ high    "
    if c >= 0.40:  return "● at-risk "
    return              "▼ low     "


async def explore():
    print()
    print("═" * W)
    print("  NOVA MEMORY EXPLORER")
    print(f"  {nova_server.SHARD_DIR}")
    print("═" * W)

    # ── 1. Load full shard index ──────────────────────────────────────────
    raw = await nova_shard_index(ShardIndexInput(per_page=200))
    data = json.loads(raw)
    shards = data.get("shards", [])
    total = len(shards)

    if not total:
        print("\n  No shards found. Check SHARD_DIR.\n")
        return

    print(f"\n  {total} shards in memory\n")

    # ── 2. Theme distribution ─────────────────────────────────────────────
    theme_counts: Counter = Counter()
    intent_counts: Counter = Counter()
    confidence_by_theme: dict[str, list[float]] = defaultdict(list)

    for s in shards:
        tags = s.get("t", [])
        theme = tags[0] if tags else "unknown"
        intent = tags[-1] if tags else "unknown"
        conf = s.get("c", 1.0)
        theme_counts[theme] += 1
        intent_counts[intent] += 1
        confidence_by_theme[theme].append(conf)

    print("  THEME DISTRIBUTION")
    print("  " + "─" * (W - 2))
    for theme, count in theme_counts.most_common(12):
        pct = count / total
        avg_conf = sum(confidence_by_theme[theme]) / len(confidence_by_theme[theme])
        print(f"  {theme:<28} {bar(pct, 18)}  {count:>4}  conf {avg_conf:.2f}")

    # ── 3. Confidence health summary ─────────────────────────────────────
    high = sum(1 for s in shards if s.get("c", 1.0) >= 0.75)
    at_risk = sum(1 for s in shards if 0.40 <= s.get("c", 1.0) < 0.75)
    low = sum(1 for s in shards if s.get("c", 1.0) < 0.40)

    print(f"\n  CONFIDENCE HEALTH")
    print("  " + "─" * (W - 2))
    print(f"  ▲ high    {bar(high/total)}  {high:>4} shards  ({high/total*100:.0f}%)")
    print(f"  ● at-risk {bar(at_risk/total)}  {at_risk:>4} shards  ({at_risk/total*100:.0f}%)")
    print(f"  ▼ low     {bar(low/total)}  {low:>4} shards  ({low/total*100:.0f}%)")

    # ── 4. Spotlight: top-confidence shards per most common theme ─────────
    top_themes = [t for t, _ in theme_counts.most_common(4)]

    print(f"\n  SPOTLIGHT — top shard per theme")
    print("  " + "─" * (W - 2))

    for theme in top_themes:
        theme_shards = sorted(
            [s for s in shards if ((s.get("t") or ["unknown"])[0] == theme)],
            key=lambda s: s.get("c", 0),
            reverse=True,
        )
        if not theme_shards:
            continue
        best = theme_shards[0]
        shard_id = best["id"]
        conf = best.get("c", 1.0)

        # Fetch full shard to get conversation content
        full_raw = await nova_shard_get(ShardGetInput(shard_id=shard_id))
        full = json.loads(full_raw)

        turns = len(full.get("conversation_history", [])) or len(full.get("turns", []))
        snip = excerpt(full)

        print(f"\n  [{theme.upper()}]  {confidence_band(conf)} conf={conf:.2f}  {turns} turns")
        print(f"  {shard_id}")
        print(f"  Q: {full.get('guiding_question', '?')}")
        print(f"  \"{snip}\"")

    # ── 5. Cross-theme search ─────────────────────────────────────────────
    queries = ["AI agents", "game design", "warfare", "creativity", "future"]

    print(f"\n\n  CROSS-THEME SEARCH — threads that connect ideas")
    print("  " + "─" * (W - 2))

    for q in queries:
        raw_search = await nova_shard_search(ShardSearchInput(query=q, top_n=3))
        results = json.loads(raw_search).get("results", [])
        if not results:
            continue
        hits = [f"{r['shard_id']} ({r['weighted_score']:.2f})" for r in results]
        print(f"\n  '{q}'")
        for h in hits:
            print(f"    → {h}")

    # ── 6. Decay watch — oldest untouched shards ──────────────────────────
    dated = [
        s for s in shards
        if s.get("created") and not any(t in s.get("t", []) for t in ("archived", "forgotten"))
    ]
    dated.sort(key=lambda s: s.get("created", "9999-99-99"))
    decay_watch = dated[:5]

    print(f"\n\n  DECAY WATCH — oldest untouched shards (run nova_shard_consolidate)")
    print("  " + "─" * (W - 2))
    for s in decay_watch:
        conf = s.get("c", 1.0)
        last = s.get("created", "?")
        print(f"  {confidence_band(conf)} {s['id']:<45} last={str(last)[:10]}")

    print()
    print("═" * W)
    print("  Done.")
    print("═" * W)
    print()


if __name__ == "__main__":
    asyncio.run(explore())
