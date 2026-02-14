import os
import json
import hashlib

SHARD_DIR = "shards"  # Change if your shard directory is elsewhere
seen_hashes = {}
duplicates = []

def normalize_content(data):
    """Returns a hash of just the meaningful message content."""
    if "messages" not in data:
        return None
    try:
        core = [(m.get("author", "").strip(), m.get("content", "").strip()) for m in data["messages"]]
        core_str = json.dumps(core, sort_keys=True)
        return hashlib.md5(core_str.encode("utf-8")).hexdigest()
    except Exception:
        return None

for filename in os.listdir(SHARD_DIR):
    if filename.endswith(".json"):
        path = os.path.join(SHARD_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                content_hash = normalize_content(data)
                if content_hash:
                    if content_hash in seen_hashes:
                        print(f"Duplicate found: {filename} == {seen_hashes[content_hash]}")
                        duplicates.append(path)
                    else:
                        seen_hashes[content_hash] = filename
        except Exception as e:
            print(f"❌ Error reading {filename}: {e}")

# Delete duplicates
for dup_path in duplicates:
    try:
        os.remove(dup_path)
        print(f"🗑️ Deleted duplicate: {dup_path}")
    except Exception as e:
        print(f"❌ Error deleting {dup_path}: {e}")
