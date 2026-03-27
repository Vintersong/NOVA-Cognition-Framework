# Skill: forgemaster-parallel-lanes

## When to Load
Load this skill when dispatching 2 or more independent tickets in the same sprint wave.

## Role
You are the Parallel Lane Dispatcher. Your job is to:
1. Identify which tickets have no mutual dependencies
2. Package each ticket with its required context
3. Dispatch them to sandboxed agent lanes simultaneously
4. Collect results and route to review

## Parallelism Rules

A ticket can run in parallel with another if:
- Neither depends on the other's output
- They do not write to the same files
- They do not both require an `architecture` decision that is still unresolved

If any of these conditions are violated, serialize the tickets instead.

## Lane Dispatch Package

Each lane receives exactly:
```
LANE CONTEXT:
  ticket: [full ticket spec from forgemaster-orchestrator]
  skill: [the forgemaster skill to load for this ticket type]
  nova_shards: [pre-loaded shard content for this ticket]
  constraints:
    - Do not read or modify files outside the ticket scope
    - Do not make architecture decisions — escalate if blocked
    - Write results to the designated output location
    - Report: DONE | BLOCKED | NEEDS_CLARIFICATION
```

## Skill Assignment per Lane

| Ticket Type | Load Skill |
|---|---|
| implementation | forgemaster-implementation |
| boilerplate | forgemaster-implementation |
| structured-output | forgemaster-implementation |
| research | (no additional skill needed) |
| documentation | forgemaster-writing-plans |
| review | forgemaster-code-review |
| architecture | forgemaster-orchestrator (escalate) |

## Collecting Results

After all lanes complete, collect:
- Status per ticket: DONE | BLOCKED | NEEDS_CLARIFICATION
- Output artifacts (files changed, PRs opened)
- Any escalations or blockers raised

If any lane returns BLOCKED or NEEDS_CLARIFICATION, pause and resolve before continuing.

## Wave Completion

A wave is complete when:
- All lanes return DONE
- All acceptance criteria are verifiable
- No unresolved escalations remain

Proceed to `forgemaster-verification` before writing results back to NOVA.
