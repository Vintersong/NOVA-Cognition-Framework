import os
import json
import re

SHARD_DIR = "shards"

def sanitize_filename(name):
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    return name[:40]

for filename in os.listdir(SHARD_DIR):
    if filename.endswith(".json"):
        old_path = os.path.join(SHARD_DIR, filename)
        try:
            with open(old_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)

            base = None
            if "shard_id" in data:
                base = data["shard_id"]
            elif "meta_tags" in data and "theme" in data["meta_tags"]:
                base = data["meta_tags"]["theme"]
            elif "guiding_question" in data:
                base = data["guiding_question"]
            else:
                base = filename.replace(".json", "")

            new_name = sanitize_filename(base) + ".json"
            new_path = os.path.join(SHARD_DIR, new_name)

            # Avoid overwrite
            counter = 1
            while os.path.exists(new_path) and new_name != filename:
                new_name = f"{sanitize_filename(base)}_{counter}.json"
                new_path = os.path.join(SHARD_DIR, new_name)
                counter += 1

            if new_name != filename:
                os.rename(old_path, new_path)
                print(f"Renamed: {filename} -> {new_name}")

        except Exception as e:
            print(f"Error reading {filename}: {e}")
