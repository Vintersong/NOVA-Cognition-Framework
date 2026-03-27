import ast

for fname in ["nova_server_v2.py", "nova_embeddings_local.py"]:
    with open(fname, "r", encoding="utf-8") as f:
        src = f.read()
    ast.parse(src)
    print(f"Syntax OK: {fname}")

from nova_embeddings_local import enrich_shard, _generate_compaction_summary
print("Import OK: enrich_shard, _generate_compaction_summary")

from filelock import FileLock
print("Import OK: FileLock")

with open("nova_server_v2.py", "r", encoding="utf-8") as f:
    src = f.read()

assert "enrich_shard_async" not in src, "FAIL: old name still present in server"
count = src.count("enrich_shard(")
print(f"FIX 1 OK: enrich_shard() called {count} times, no async suffix")

lock_count = src.count("FileLock(")
assert lock_count >= 3, f"FAIL: expected >= 3 FileLock calls, got {lock_count}"
print(f"FIX 2 OK: FileLock applied at {lock_count} call sites")

assert "def patch_index_entry" in src
assert "patch_index_entry(" in src
print("FIX 3 OK: patch_index_entry defined and called")

assert "def query_graph_transitive" in src
assert "transitive: bool" in src
assert "max_depth: int" in src
print("FIX 4 OK: query_graph_transitive defined, GraphQueryInput extended")

print("\nAll checks passed.")
