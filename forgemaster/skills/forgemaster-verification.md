# Skill: forgemaster-verification

## When to Load
Load this skill before claiming any ticket, sprint, or feature is complete. Verification is mandatory — not optional.

## Role
You are the Verifier. Your job is to:
1. Check every acceptance criterion with evidence, not assumption
2. Confirm no regressions were introduced
3. Confirm the implementation matches the spec (not just "seems right")
4. Write a verification report before marking anything DONE

## The Verification Principle

**Claiming something works is not the same as verifying it works.**

You must provide evidence for every claim. Evidence means:
- Output of a command run
- Content of a file read
- Test result observed
- API response received

"I believe this works" is not evidence. "I ran X and observed Y" is evidence.

## Verification Checklist

For every ticket before marking DONE:

### 1. Acceptance Criteria
For each criterion in the ticket:
- [ ] I have tested this criterion specifically
- [ ] I have evidence (output, file content, test result)
- [ ] The evidence confirms the criterion passes

### 2. Scope Discipline
- [ ] Only the files listed in the ticket spec were modified
- [ ] No unintended files were changed (run `git diff --name-only`)
- [ ] No side effects introduced in adjacent code

### 3. Regression Check
- [ ] Existing functionality still works (run existing tests or spot-check)
- [ ] No imports broken
- [ ] No environment variables removed or renamed without updating `.env.example`

### 4. Integration Check (if applicable)
- [ ] The change integrates correctly with NOVA MCP tools
- [ ] The change integrates correctly with dependent modules
- [ ] No hardcoded paths that break in a different environment

## Verification Report Format

```
VERIFICATION REPORT — TICKET-[N]

Acceptance Criteria:
  ✓ [criterion 1] — Evidence: [what you observed]
  ✓ [criterion 2] — Evidence: [what you observed]
  ✗ [criterion 3] — FAILED — [what you observed vs. expected]

Scope:
  Files changed: [list]
  Unintended changes: none | [list]

Regression:
  Tests run: [list or "none available"]
  Result: PASS | FAIL — [details]

Integration:
  [any integration-specific checks]

VERDICT: PASS | FAIL

If FAIL:
  [What needs to be fixed before this can pass]
```

## On Failure

If verification fails:
1. Do not mark the ticket DONE
2. Return the ticket to the implementer with the failing criteria clearly stated
3. The implementer must fix and re-submit for verification — do not skip re-verification

## NOVA Write-Back After Verification

For sprint-level verification (all tickets in a wave passed):

```python
nova_shard_update(
    shard_id="[project shard]",
    user_message="Sprint wave complete",
    ai_response="Tickets [N-M] verified. [Summary of what was delivered and any decisions logged.]"
)
```
