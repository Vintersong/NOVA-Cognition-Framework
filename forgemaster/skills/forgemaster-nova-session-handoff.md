# Skill: forgemaster-nova-session-handoff

## When to Load
Load this skill when approaching context limit, ending a session, or handing work to another agent.

**This skill is not optional.** Without a handoff write, the next session starts from zero. NOVA's entire purpose is to prevent this.

## Role
You are the Session Handoff Manager. Your job is to:
1. Capture current state precisely before the session ends
2. Write it to the correct NOVA shard
3. Ensure the next session can continue without needing to reconstruct context

## When to Trigger

Trigger a handoff write when any of these are true:
- Context window is ~60% full
- You are about to close the session intentionally
- A lane agent has finished its work and is returning results
- The human has approved a PR and work continues next session

Do not wait until the context is full — write the handoff early enough to do it properly.

## Handoff Write Protocol

### Step 1: Locate the project shard

```python
nova_shard_interact(message="[project name] current state")
# Note the shard_id from the result
```

### Step 2: Write the handoff

```python
nova_shard_update(
    shard_id="[project-shard-id]",
    user_message="Session handoff — [date]",
    ai_response="""
CURRENT STATE:
  Branch: [branch name]
  Last completed ticket: TICKET-[N] — [title]
  Tests: [passing | failing | not run]
  Build: [clean | broken at X]

IN PROGRESS:
  TICKET-[N+1]: [title]
  Started: [what has been done so far]
  Remaining: [what still needs to be done]
  Blocker (if any): [exact blocker description]

DECISIONS MADE THIS SESSION:
  - [Decision 1]: [what was decided and why]
  - [Decision 2]: [what was decided and why]

KEY FILES TOUCHED:
  - [file path]: [what changed]

NEXT ACTION (first thing to do next session):
  [Single, specific, unambiguous instruction for the next agent or session]
  Example: "Run TICKET-4 implementation of nova_graph_relate tool.
            Spec is in the sprint plan. Branch is feat/ticket-4-graph-relate.
            Start by reading mcp/nova_server.py lines 180–220."
"""
)
```

### Step 3: Confirm the write

After writing, call `nova_shard_interact` again with the same query to confirm the handoff content is retrievable.

## Session Resume Protocol

At the start of a new session, always:

```python
# 1. Load project context
nova_shard_interact(message="[project name] current state")

# 2. Read the NEXT ACTION from the handoff
# 3. Execute that action — do not invent your own starting point
```

## Handoff Quality Checklist

Before ending the session, confirm:
- [ ] The CURRENT STATE section accurately reflects what is true right now
- [ ] The NEXT ACTION is specific enough that a new agent can act on it without asking questions
- [ ] Any DECISIONS MADE are captured — not just "what" but "why"
- [ ] Any BLOCKERS are stated precisely (not "it didn't work" but "X returned Y when Z was expected")
- [ ] The handoff was written to the correct shard (not a generic shard)
- [ ] The write was confirmed retrievable

## Common Mistakes

**Too vague**: "Worked on the implementation. Need to finish it."
→ Fix: Specify exactly what was done, what remains, and the first command to run next session.

**Missing decisions**: Not capturing why an architectural choice was made.
→ Fix: Always write the rationale, not just the outcome.

**Wrong shard**: Writing to a general conversation shard instead of the project shard.
→ Fix: Always use the project-specific shard ID.

**Skipping the handoff**: "I'll write it next session."
→ There is no next session without this write. Do it now.
