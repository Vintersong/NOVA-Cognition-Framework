---
name: nova-consolidate-gate
enabled: true
event: stop
pattern: .*
action: warn
---

**NOVA Consolidation Check**

If you called `nova_shard_update` or `nova_shard_create` 3 or more times this session, run consolidation before stopping:

```python
nova_shard_consolidate()
```

Consolidation runs: confidence decay on stale shards → compaction of bloated shards → top-10 merge candidate suggestions.

**Skip if:**
- Fewer than 3 shard writes happened this session
- Consolidation was already run this session

**Do not skip if:**
- You ran a full sprint (sprint = multiple shard updates by definition)
- Any shard was created fresh this session (new shards need their first decay baseline set)
