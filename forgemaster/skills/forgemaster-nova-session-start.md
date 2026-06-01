@@verification: unverified
@@capabilities: *

# Skill: forgemaster-nova-session-start

## When to Load
Load this skill at the start of every session before doing any work. This is the mandatory session initialization ritual.

**This skill is not optional.** Without it, the session operates without project context and risks duplicating work, contradicting prior decisions, or missing active blockers.

## Role
You are the Session Initializer. Your job is to:
1. Load the NOVA skill definition
2. Pull relevant project context into the active context window
3. Surface any open threads, blockers, or handoff notes from the last session
4. Orient to the current state before accepting any task

## Initialization Protocol

### Step 1: Read the NOVA skill definition

```python
Read("mcp/SKILL.md")
```

This loads the current tool list, confidence system rules, and reasoning methodology. Do not skip — tool signatures change.

### Step 2: Load project context

```python
nova_shard_interact(message="[project name] current state")
```

Replace `[project name]` with the actual project name (e.g., "NOVA infrastructure", "forgemaster sprint", "shard pipeline").

If the project name is not known yet, use:
```python
nova_shard_interact(message="current state open work priorities")
```

### Step 3: Surface active threads

After `nova_shard_interact` returns, scan the loaded shards for:
- Any shard with `NEXT ACTION` or `IN PROGRESS` fields (from prior handoffs)
- Shards tagged `recent` — these are the active work front
- Any `contradicts` graph relations — surface these immediately

Report to the user:
```
Session loaded. Active threads:
- [shard_id]: [one-line summary of what's in progress]
- [shard_id]: [open decision or blocker]

Suggested starting point: [NEXT ACTION from most recent handoff, or "none found — state your task"]
```

### Step 4: Check consolidation status (if applicable)

If the last consolidation run was more than 3 sprints ago, note it:
```
⚠️ Consolidation due. Run nova_shard_consolidate() before the next sprint.
```

## What to Do If No Shards Load

If `nova_shard_interact` returns no shards or an empty result:
1. This may be a fresh install — read `mcp/ONBOARDING.md` and run the onboarding flow
2. Or the shard index is stale — run `nova_shard_index(rebuild=True)` to rebuild it
3. Do not proceed with work until context is loaded

## Initialization Quality Checklist

Before accepting any task, confirm:
- [ ] `mcp/SKILL.md` has been read this session
- [ ] `nova_shard_interact` has been called with a relevant query
- [ ] Any `NEXT ACTION` from a prior handoff has been surfaced to the user
- [ ] Any `contradicts` graph relations in loaded shards have been flagged
- [ ] Consolidation status has been noted if overdue

## Common Mistakes

**Skipping SKILL.md**: Tool signatures and env vars change. Reading SKILL.md takes 10 seconds and prevents silent tool misuse.

**Generic interact query**: `nova_shard_interact(message="hello")` will load low-relevance shards. Use project-specific language.

**Starting work before context loads**: The shard_interact result is the map. Working without it means navigating blind.

**Ignoring handoff NEXT ACTION**: The prior session identified the exact resume point. Overriding it without reading it first wastes the handoff.
