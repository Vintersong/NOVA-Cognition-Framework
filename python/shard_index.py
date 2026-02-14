import os
import json

SHARD_DIR = "shards"
INDEX_FILE = "shard_index.json"

def load_shard_index() -> dict:
    if not os.path.exists(INDEX_FILE):
        return {"shards": []}
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_shard_index(index: dict):
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

def update_index_with_missing_shards() -> dict:
    index = load_shard_index()
    known_shards = set(index.get("shards", []))
    current_files = {f for f in os.listdir(SHARD_DIR) if f.endswith(".json")}

    missing = current_files - known_shards
    if missing:
        index["shards"].extend(sorted(missing))
        save_shard_index(index)
        print(f"✅ Added {len(missing)} missing shards to index.")
    else:
        print("✅ Index already up to date.")

    return index
