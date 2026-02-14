import os
import json
from datetime import datetime, timedelta

SHARD_DIR = "shards"
INDEX_FILE = "shard_index.json"

def load_shard(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load {filepath}: {e}")
        return None

def classify_tags(shard):
    tags = []
    now = datetime.now()

    meta = shard.get("meta_tags", {})
    usage_count = meta.get("usage_count", 0)
    last_used_str = meta.get("last_used")

    # Parse timestamp
    if last_used_str:
        try:
            last_used = datetime.fromisoformat(last_used_str)
            if now - last_used < timedelta(days=3):
                tags.append("recent")
            if now - last_used > timedelta(days=14):
                tags.append("stale")
        except Exception:
            pass

    if usage_count > 10:
        tags.append("frequently_used")

    return tags

def main():
    index = {}

    for fname in os.listdir(SHARD_DIR):
        if not fname.endswith(".json"):
            continue

        fpath = os.path.join(SHARD_DIR, fname)
        shard = load_shard(fpath)
        if not shard:
            continue

        shard_id = shard.get("shard_id", fname.replace(".json", ""))
        guiding_question = shard.get("guiding_question", "")
        meta_tags = shard.get("meta_tags", {})

        entry = {
            "shard_id": shard_id,
            "filename": fname,
            "guiding_question": guiding_question,
            "tags": classify_tags(shard),
            "meta": meta_tags
        }

        index[shard_id] = entry

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
        print(f"✅ Index updated with {len(index)} shards -> {INDEX_FILE}")

if __name__ == "__main__":
    main()
