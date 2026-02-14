"""
context_extractor.py — Semantic enrichment for NOVA shards.

Generates GPT-4 summaries, topic tags, and ada-002 embeddings
for each shard. Run periodically or after adding new shards.
The 'context' field it writes is used by main.py and the MCP
server for semantic search.

Usage:
    python context_extractor.py              # Enrich all shards
    python context_extractor.py --force      # Re-enrich even already-enriched shards
"""

import os
import sys
import json
import time
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

from shard_index import SHARD_DIR, update_index

# === Environment ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("✗ OPENAI_API_KEY not set in .env")

client = OpenAI(api_key=OPENAI_API_KEY)
os.makedirs(SHARD_DIR, exist_ok=True)

MODEL_EMBED = "text-embedding-ada-002"
MODEL_GPT = "gpt-4"


def get_shard_files() -> list[str]:
    return sorted(f for f in os.listdir(SHARD_DIR) if f.endswith(".json"))


def load_shard(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ✗ Failed to load {path}: {e}")
        return None


def save_shard(path: str, shard: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(shard, f, indent=2)


def generate_context(content: str) -> dict | None:
    """Ask GPT-4 to produce a structured summary, topics, and type for a shard."""
    prompt = (
        "You are NOVA, a memory processor. Analyze the following shard content "
        "and respond with ONLY a JSON object (no markdown, no backticks) containing:\n"
        '- "summary": A 1-2 sentence summary of the shard\'s purpose\n'
        '- "topics": A list of 3-6 topic tags as strings\n'
        '- "conversation_type": The type (e.g., debugging, philosophy, design, memory reflection)\n\n'
        f"Shard content:\n{content[:12000]}"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL_GPT,
            messages=[
                {"role": "system", "content": "You are a context analysis assistant. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  ⚠ GPT returned non-JSON, attempting fallback parse")
        return _fallback_parse(raw)
    except Exception as e:
        print(f"  ✗ GPT context generation failed: {e}")
        return None


def _fallback_parse(raw: str) -> dict | None:
    """Best-effort extraction from non-JSON GPT output."""
    result = {"summary": "", "topics": [], "conversation_type": ""}
    lines = raw.strip().splitlines()
    for line in lines:
        line = line.strip("- • ").strip()
        lower = line.lower()
        if "summary" in lower and ":" in line:
            result["summary"] = line.split(":", 1)[1].strip()
        elif "topic" in lower and ":" in line:
            tag_str = line.split(":", 1)[1].strip()
            result["topics"] = [t.strip() for t in tag_str.split(",")]
        elif "type" in lower and ":" in line:
            result["conversation_type"] = line.split(":", 1)[1].strip()
        elif not result["summary"]:
            result["summary"] = line

    return result if result["summary"] else None


def get_embedding(text: str) -> list | None:
    try:
        response = client.embeddings.create(model=MODEL_EMBED, input=[text])
        return response.data[0].embedding
    except Exception as e:
        print(f"  ✗ Embedding error: {e}")
        return None


def process_shards(force: bool = False):
    files = get_shard_files()
    total = len(files)
    enriched = 0
    skipped = 0

    print(f"Found {total} shards in {SHARD_DIR}/\n")

    for i, fname in enumerate(files, 1):
        fpath = os.path.join(SHARD_DIR, fname)
        shard = load_shard(fpath)
        if not shard:
            continue

        # Skip already-enriched unless forced
        if not force and shard.get("context", {}).get("embedding"):
            print(f"[{i}/{total}] {fname} — already enriched, skipping")
            skipped += 1
            continue

        print(f"[{i}/{total}] {fname} — enriching...")

        text = json.dumps(shard, ensure_ascii=False)
        context = generate_context(text)
        if not context or not context.get("summary"):
            print(f"  ⚠ No valid context generated, skipping embedding")
            continue

        embedding = get_embedding(context["summary"])

        shard["context"] = {
            "summary": context.get("summary", ""),
            "topics": context.get("topics", []),
            "conversation_type": context.get("conversation_type", ""),
            "embedding": embedding,
            "last_context_update": datetime.utcnow().isoformat()
        }

        save_shard(fpath, shard)
        enriched += 1
        print(f"  ✓ Enriched")
        time.sleep(1.2)  # Rate limit buffer

    print(f"\nDone. Enriched: {enriched}, Skipped: {skipped}, Failed: {total - enriched - skipped}")

    # Rebuild index to reflect new context data
    update_index()


if __name__ == "__main__":
    force = "--force" in sys.argv
    process_shards(force=force)
