import json
import os
from pathlib import Path

# Test shard I/O directly
shards_dir = "shards"
os.makedirs(shards_dir, exist_ok=True)

# List existing shards
shards = list(Path(shards_dir).glob("*.json"))
print(f"✓ Shard directory exists: {shards_dir}")
print(f"✓ Found {len(shards)} existing shards")
if shards:
    print(f"\nSample shards:")
    for shard in shards[:3]:
        try:
            with open(shard) as f:
                data = json.load(f)
                guiding_q = data.get("guiding_question", "N/A")[:60]
                turns = len(data.get("conversation_history", []))
                print(f"  - {shard.name}: {guiding_q}... ({turns} turns)")
        except Exception as e:
            print(f"  - {shard.name}: ERROR - {e}")

# Check index
index_file = "shard_index.json"
if os.path.exists(index_file):
    with open(index_file) as f:
        index = json.load(f)
    print(f"\n✓ Shard index exists: {len(index)} entries")
else:
    print(f"\n✗ Shard index not found")

# Check graph
graph_file = "shard_graph.json"
if os.path.exists(graph_file):
    with open(graph_file) as f:
        graph = json.load(f)
    print(f"✓ Knowledge graph exists: {len(graph.get('shards', {}))} nodes")
else:
    print(f"✗ Knowledge graph not found")
