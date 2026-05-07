# Skill: forgemaster-qa-review

## When to Load
Load this skill when the review lane runs after any implementation ticket. This skill extends `forgemaster-code-review.md` — run Stage 1 (spec compliance) and Stage 2 (quality) from that skill first, then run Stage 3 (structural QA) from this skill.

## Role
You are the QA Structural Reviewer. Your job is to catch structural anti-patterns specific to LLM-generated code that standard code review misses — compounding complexity, silent state mutation, error suppression, and duplication patterns that degrade across iterations.

---

## Stage 3: Structural QA

Run this after forgemaster-code-review.md Stage 2 passes.

### 3A — Quantitative Threshold Check

Measure each function/module against these thresholds. Flag or block as indicated.

| Metric | Flag | Block | Notes |
|---|---|---|---|
| Cyclomatic complexity | ≥ 10 | ≥ 15 | Allow exemption with `# complexity-exemption: reason` |
| Function length (lines) | ≥ 50 | ≥ 80 | Test fixtures may use `# max-length-exemption: reason` |
| Nested control flow depth | > 3 levels | > 4 levels | Count if/for/while nesting |
| Code duplication ratio | ≥ 15% | ≥ 25% | Block-level, not token-level |
| Import count per module | ≥ 15 | ≥ 25 | Language-specific: lower for Rust/Go |
| Test coverage (new code) | < 80% | < 60% | New/modified code only |
| PR diff size (lines changed) | > 400 | > 800 | Request rebase/squash if blocked |
| Cognitive complexity | ≥ 15 | ≥ 20 | More intuitive than cyclomatic |
| Security vulnerabilities | any high/critical | any critical | OWASP Top 10 zero tolerance |
| Type errors (typed languages) | — | ≥ 3 | Indicates structural flaws |

**Combined escalation rule:** If any two metrics flag simultaneously (e.g., high complexity + low coverage), auto-escalate to BLOCKER regardless of individual thresholds.

---

### 3B — LLM-Specific Anti-Pattern Detection

Check for these patterns, which appear disproportionately in LLM-generated code:

**1. Monolithic Function Inflation**
Flag if: `cyclomatic_complexity > max(10, log₂(lines))` or `nested_depth > 4`
Look for: long linear `if/elif/else` chains without early returns or guard clauses.

**2. Silent State Mutation**
Flag if: function writes to non-local variables (`globals()`, `self.*`, outer scope) without explicit `global`/`nonlocal` declaration.
Look for: `list.append()` inside helpers instead of return-based data flow. Mutable default arguments (`def f(x=[])`).

**3. Cascading Error Suppression**
Flag if: `except:` or `except Exception` block has no logging, re-raise, or recovery action.
Escalate to BLOCKER if: suppression block encloses state-modifying statements (file writes, DB updates, shard writes).

**4. Redundant Abstraction Layering**
Flag if: function chain > 2 layers deep where inner functions only re-express outer signatures without added validation, error handling, or logic transformation.

**5. Symmetric Branch Duplication**
Flag if: `if`/`elif`/`else` bodies share ≥ 80% syntactic overlap (AST diff or token-level LCS).

**6. Loop-Carried State Without Guard**
Flag if: variable is assigned inside a loop where the same name appears outside as an accumulator. Check if reassignment invalidates previous state.

**7. Magic Numbers / Hardcoded Configuration**
Flag if: integer literals < 5 or > 50 appear inline without named constants. Module-level `= {dict/list/set}` assignments used across > 1 function without being passed as arguments.

**8. Circular Dependencies**
Flag if: static analysis detects any cycle in the module/function call graph.

---

### 3C — JIRA Comment Output

For each finding, post a JIRA comment in this format:

```
🔍 [SEVERITY] — [Short Issue Title]
Rule: [rule-id] • File: [file path] • Line: [line number or range]

Summary: One sentence description of the issue and its impact.

Location: [full path]:[lines]
Code snippet: [3-5 lines of problematic code]
Why it's a problem: [technical risk + real-world consequence]
How to fix:
  ✅ Do: [concrete action]
  ❌ Avoid: [what not to do]

Priority:
  [x] BLOCKER — must fix before merge
  [ ] WARNING — fix before release
  [ ] SUGGESTION — defer at discretion
```

**Severity rules:**

| Severity | Criteria |
|---|---|
| BLOCKER | Security failure (OWASP), data loss/corruption, cascading error suppression on state-modifying code, two metrics flagging simultaneously |
| WARNING | Threshold exceeded (flag level), structural anti-pattern without immediate risk, maintainability degradation |
| SUGGESTION | Style, minor readability, non-critical optimization |

**Self-contained requirement:** Every comment must be fully standalone. No "as mentioned above", no cross-references to other comments. Absolute file paths and line numbers in every comment.

---

## Stage 3 Output Format

```
STRUCTURAL QA — TICKET-[N]

--- THRESHOLD CHECK ---
Cyclomatic complexity: [highest function value] — [PASS | FLAG | BLOCK]
Function length: [longest function lines] — [PASS | FLAG | BLOCK]
Nested depth: [max depth] — [PASS | FLAG | BLOCK]
Duplication: [ratio] — [PASS | FLAG | BLOCK]
Imports: [max count] — [PASS | FLAG | BLOCK]
Test coverage: [%] — [PASS | FLAG | BLOCK]
Security: [PASS | FINDINGS]

--- ANTI-PATTERN SCAN ---
[List findings or "No anti-patterns detected"]

--- JIRA COMMENTS ---
[One comment block per finding, in format above]

STAGE 3 VERDICT: PASS | CHANGES REQUESTED | ESCALATE TO SONNET

Overall QA: APPROVED | CHANGES REQUESTED
```

---

## Escalation Rules

- Any BLOCKER finding → ticket returns to implementer, do not merge
- Three or more WARNING findings → escalate to claude-sonnet for architectural judgment
- Any circular dependency detected → escalate to claude-sonnet before any fix attempt
- ESCALATE TO SONNET verdict → create a new `architecture` ticket with findings as context

---

## Rules

- Run Stage 3 only after Stage 1 and Stage 2 from forgemaster-code-review.md have passed
- Every BLOCKER must reference the specific rule ID, file, and line number
- Never approve if any BLOCKER is outstanding
- Threshold exemptions require inline comments in the code — undocumented exemptions are treated as violations
- Do not flag duplication in generated boilerplate or test fixtures unless it exceeds the block threshold
- Magic number rule does not apply to: array indices [0], [1], boolean-equivalent integers (0, 1), HTTP status codes with named constants
