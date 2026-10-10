@@verification: unverified
@@capabilities: *

# Skill: forgemaster-nova-shard-triage

## When to Load
Load this skill when the shard corpus feels cluttered, after a consolidation run surfaces merge candidates, or on a scheduled hygiene pass (recommended every 10–15 sprints).

## Role
You are the Shard Triager. Your job is to:
1. Survey the full shard index for decay, redundancy, and rot
2. Flag shards that need action: archive, merge, forget, or revive
3. Execute the decisions with the user's confirmation
4. Leave the corpus leaner and higher-confidence than you found it

## Triage Protocol

### Step 1: Get the full index

```python
nova_shard_index()
```

This returns compact metadata rows for every shard: shard_id, guiding_question, confidence, tags, last_used, usage_count.

Do not load full shard bodies yet — work from metadata only.

### Step 2: Flag candidates

Sort shards into four buckets based on metadata:

**Archive candidates** — safe to soft-hide, content preserved:
- `confidence < 0.4` AND `last_used > 30 days ago`
- Tagged `stale` AND `usage_count < 3`
- Project shards for work that is clearly complete and closed

**Merge candidates** — two shards covering the same territory:
- Similar guiding questions (surface judgment, not similarity score)
- One shard is a subset of another
- `nova_shard_consolidate` already flagged these as pairs

**Forget candidates** — hard exclude, intentional burial:
- Content contradicts current architecture and is actively misleading
- Imported data that turned out to be noise (e.g., off-topic intake shards)
- Duplicates of already-merged shards left over from manual work

**Revive candidates** — worth reading again despite low confidence:
- `confidence < 0.4` BUT `usage_count > 10` (was heavily used, now stale — check if still relevant)
- `low_confidence` tag BUT guiding question matches active work

### Step 3: Present findings to user

Report the buckets before acting:

```
SHARD TRIAGE REPORT
===================
Archive candidates (N):
  [shard_id] "[guiding_question]" — confidence 0.X, last used DD days ago

Merge candidates (N pairs):
  [shard_id_A] + [shard_id_B] — "[reason they overlap]"

Forget candidates (N):
  [shard_id] "[guiding_question]" — reason: [why it's actively harmful to keep]

Revive candidates (N):
  [shard_id] "[guiding_question]" — usage_count X, may still be relevant

Awaiting your decision on each bucket before executing.
```

Do not execute any action until the user confirms or modifies the list.

### Step 4: Execute confirmed decisions

**For archives** (asks for approval; takes no reason — record it in the Step 6 summary):
```python
nova_shard_archive(shard_id="[id]")
```

**For merges:**
```python
nova_shard_merge(
    shard_ids="[id_A],[id_B]",                       # comma-separated string
    new_guiding_question="[combined north-star question]",
    new_theme="[theme]",
    archive_originals=True,                          # optional; default False
)
```

**For forgets** (asks for approval):
```python
nova_shard_forget(shard_id="[id]", reason="[why this is being excluded]")
```

**For revivals** — load and assess before deciding:
```python
nova_shard_get_full(shard_id="[id]")
# Read content, then either:
# - raise confidence with a validation event (appending a turn never changes confidence):
#   nova_shard_validate(shard_id="[id]", source_type="peer_validated",
#                       validator="[who]", mechanism="triage_review", confidence_delta=0.1)
#   source_type cannot rank below the shard's current one — it is refused, not downgraded
# - or move it to archive/forget if content is stale
```

### Step 5: Post-triage maintenance

No action needed: NÓTT runs decay, compaction and merge detection on its own
(after a sprint and on a shard-count threshold). To see what the last cycle found:

```python
nova_shard_consolidate(dry_run=True)   # reads the last report; starts nothing
```

Force a cycle with `nova_shard_consolidate()` only if you need merge candidates
recomputed now — it asks for approval.

### Step 6: Write triage summary to project shard

```python
nova_shard_update(
    shard_id="[project shard id]",
    user_message="Shard triage — [date]",
    ai_response="""
TRIAGE COMPLETE
  Archived: N shards
  Merged: N pairs → N meta-shards
  Forgotten: N shards
  Revived: N shards
  Corpus size before: X / after: Y
  Next triage recommended: [date ~10 sprints out]
"""
)
```

## Triage Quality Checklist

Before closing the triage session:
- [ ] All four buckets were assessed (even if some are empty)
- [ ] No shard was forgotten without an explicit reason logged
- [ ] All merges produced a meta-shard with a clear guiding question
- [ ] Archive and forget reasons were recorded in the triage summary
- [ ] Triage summary was written to the project shard

## Common Mistakes

**Archiving too aggressively**: Archive is soft — content is preserved and recoverable. Forget is hard. When in doubt, archive.

**Merging unlike shards**: A merge should produce a shard that is more coherent than either source. If the merged guiding question is awkward, the shards don't belong together.

**Skipping the revive bucket**: High-usage stale shards often contain the most valuable older context. Check them before burying.

**Not confirming with user before execution**: Triage decisions are irreversible (forget) or structural (merge). Always present the plan and wait for confirmation.
