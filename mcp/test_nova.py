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
    nova_shard_list,
    nova_shard_search,
    nova_shard_get,
    ShardSearchInput,
    ShardGetInput,
)

W  = 72  # output width


def bar(value: float, width: int = 20) -> str:
    filled = int(round(value * width))
    return "█" * filled + "░" * (width - filled)


def excerpt(shard_get_result: dict, max_chars: int = 200) -> str:
    """Pull the first meaningful fragment from nova_shard_get output."""
    for frag in shard_get_result.get("fragments", []):
        # fragments look like '[SHARD: id] User: text' or '[SHARD: id] NOVA: text'
        if "] User: " in frag:
            text = frag.split("] User: ", 1)[-1].strip()
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
    raw = await nova_shard_list()
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
        # nova_shard_list flattens theme/intent to top-level strings,
        # but last_used lives inside meta (the full meta_tags dict)
        theme  = s.get("theme") or s.get("meta", {}).get("theme") or "unknown"
        intent = s.get("intent") or s.get("meta", {}).get("intent") or "unknown"
        conf   = s.get("confidence", 1.0)
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
    high    = sum(1 for s in shards if s.get("confidence", 1.0) >= 0.75)
    at_risk = sum(1 for s in shards if 0.40 <= s.get("confidence", 1.0) < 0.75)
    low     = sum(1 for s in shards if s.get("confidence", 1.0) < 0.40)

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
            [s for s in shards if (s.get("theme") or s.get("meta", {}).get("theme") or "unknown") == theme],
            key=lambda s: s.get("confidence", 0),
            reverse=True,
        )
        if not theme_shards:
            continue
        best = theme_shards[0]
        shard_id = best["shard_id"]
        conf = best.get("confidence", 1.0)

        # Fetch full shard to get conversation content
        full_raw = await nova_shard_get(ShardGetInput(shard_id=shard_id))
        full = json.loads(full_raw)

        turns = full.get("fragment_count", 0) // 2  # each turn = 1 user + 1 ai fragment
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
        if (s.get("last_used") or s.get("meta", {}).get("last_used")) and not any(
            t in s.get("tags", []) for t in ("archived", "forgotten")
        )
    ]
    dated.sort(key=lambda s: (s.get("last_used") or s.get("meta", {}).get("last_used", "9")))
    decay_watch = dated[:5]

    print(f"\n\n  DECAY WATCH — oldest untouched shards (run nova_shard_consolidate)")
    print("  " + "─" * (W - 2))
    for s in decay_watch:
        conf = s.get("confidence", 1.0)
        last = s.get("last_used") or s.get("meta", {}).get("last_used", "?")
        print(f"  {confidence_band(conf)} {s['shard_id']:<45} last={str(last)[:10]}")

    print()
    print("═" * W)
    print("  Done.")
    print("═" * W)
    print()


if __name__ == "__main__":
    asyncio.run(explore())
