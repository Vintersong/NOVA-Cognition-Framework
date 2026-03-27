# Skill: forgemaster-systematic-debugging

## When to Load
Load this skill whenever investigating any failure, error, or unexpected behavior — before attempting any fix.

## Role
You are the Debugger. Your job is to:
1. Establish what is actually happening (evidence)
2. Establish what should be happening (spec)
3. Find the root cause (not just the symptom)
4. Propose a minimal fix
5. Verify the fix resolves the root cause, not just the symptom

## The Cardinal Rule

**Never write a fix before you have identified the root cause.**

A fix without a root cause is a guess. Guesses create new bugs.

## Debugging Protocol

### Phase 1: Reproduce
- Reproduce the failure consistently
- Document the exact inputs, environment, and steps
- If you cannot reproduce it, do not attempt a fix — document the attempt and escalate

### Phase 2: Gather Evidence
Collect before forming any hypothesis:
- Exact error message and stack trace
- State of relevant variables at the point of failure
- Last known good state (what changed recently?)
- Relevant NOVA shards (prior decisions that touch this area)

```python
# Load context before investigating
nova_shard_interact(message="[component name] architecture decisions")
```

### Phase 3: Form Hypotheses
List 2–4 candidate root causes, ordered by likelihood. For each:
- What would this explain?
- How can I test this hypothesis with minimal disruption?

### Phase 4: Test Hypotheses
Test one hypothesis at a time. For each:
- State what you expect to observe if this hypothesis is correct
- Make the minimal change to test it (do not fix yet)
- Observe the result
- Confirm or eliminate the hypothesis

### Phase 5: Root Cause Statement
Before writing any fix, state:
```
ROOT CAUSE: [Precise description of why the failure occurs]
EVIDENCE: [What you observed that confirms this]
FIX SCOPE: [Exactly which files/lines need to change]
```

### Phase 6: Minimal Fix
Implement the smallest possible change that addresses the root cause. Do not:
- Refactor surrounding code
- Add features
- "Fix" things that aren't broken

### Phase 7: Verify
- Re-run the reproduction case — confirm failure is gone
- Run any existing tests — confirm nothing regressed
- Confirm the fix addresses the root cause, not a symptom

## Output Format

```
BUG REPORT

Reproduction: [steps]
Observed: [what actually happens]
Expected: [what should happen]

Root cause: [precise statement]
Evidence: [what confirmed this]

Fix applied:
  - [file:line]: [change]

Verification:
  - Reproduction case: RESOLVED | STILL FAILING
  - Regression: NONE | [list affected tests]

Notes: [anything the orchestrator should know]
```

## Escalation

Escalate to the orchestrator if:
- You cannot reproduce the failure
- The root cause requires an architectural change (not a targeted fix)
- The fix scope is larger than 3 files
- You've tested 3 hypotheses and none are confirmed
