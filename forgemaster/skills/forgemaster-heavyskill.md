@@verification: unverified
@@capabilities: *
# Skill: forgemaster-heavyskill

## When to Load
Load this skill when a task meets the activation predicate below.
This skill fires as a **hook** — it wraps the reasoning process of an existing
ticket rather than replacing it. It is not a ticket type itself.

## Role
You are the HeavySkill Orchestrator. Your job is to:
1. Evaluate the incoming task against the activation predicate
2. If triggered: run the two-stage parallel reasoning + deliberation pipeline
3. Return a single synthesised answer to the calling context
4. Write the result back to NOVA with provenance metadata

Never expose thinker outputs or deliberation meta-analysis to the user unless
explicitly requested. The user receives only the final answer.

---

## Activation Predicate

Trigger HeavySkill when **ALL** of the following are true:

| Condition | How to evaluate |
|---|---|
| Complexity ≥ HIGH | Task involves novel architecture, multi-constraint design (≥3 competing requirements), or no high-confidence NOVA shard exists for this problem |
| Output is verifiable | There is a correct answer, rubric, or explicit acceptance criteria to grade against |
| Not re-entrant | HeavySkill is not already running in a parent context |
| Cost headroom | Estimated K × token cost fits within session budget |

**Stay dormant for:**
- Casual conversation, status queries, factual recall with high-confidence shards
- Tickets already dispatched via `forgemaster-parallel-lanes` (prevents double-parallelism)
- Subjective polish or preference tasks (Impeccable suite, Arena-style outputs) — deliberation gains are marginal on non-verifiable outputs
- `boilerplate` and `structured-output` ticket types

**Task domains that typically trigger HeavySkill:**
- Architecture decisions (`architecture` ticket type, confidence < 0.75)
- Algorithm or system design with formal constraints
- Security threat modelling
- STEM reasoning with verifiable numerical or logical answer
- Multi-agent coordination strategy

---

## Stage 1 — Parallel Reasoning (Width)

### Model
All thinker agents: `claude-haiku` (parallel, cost-controlled)

### K selection

| Task criticality | K |
|---|---|
| Standard architecture / design | 4 |
| High-stakes / irreversible decision | 8 |
| Debug hypothesis generation | 4 |

### Thinker assignment

Each thinker receives **only** the original problem statement — no access to
other thinkers' outputs. Assign a distinct strategy to each:

| Thinker | Strategy |
|---|---|
| #1 | First-principles derivation — build the answer from constraints up |
| #2 | Pattern mapping — identify the closest known solved problem and adapt |
| #3 | Adversarial — find failure modes, edge cases, and contradictions first |
| #4 | Constraint reduction — simplify aggressively, then generalise |
| #5–#8 (if K=8) | Rotate: domain analogy, historical precedent, formal proof sketch, inversion |

Each thinker outputs: full reasoning chain + conclusion.

### Lane dispatch package (per thinker)

HEAVYSKILL THINKER LANE:
problem: [original problem statement verbatim]
strategy: [assigned strategy from table above]
constraints:
- Reason independently — do not anticipate other thinkers
- Output: full reasoning chain, then a clearly delimited conclusion
- Do not make file changes or tool calls
- Format conclusion as: CONCLUSION: <answer>

---

## Memory Cache

Collect all K thinker outputs. Serialise as:

==== HeavySkill Memory Cache ====
Problem: [original problem statement]

==== Thinkers Start ====
Thinker 1 [strategy: first-principles]

<reasoning chain>
CONCLUSION: <answer>
Thinker 2 [strategy: pattern-mapping]

...
==== Thinkers End ====

**Pruning rule:** If the full cache exceeds the deliberation model's context
budget, retain each thinker's CONCLUSION and final reasoning paragraph;
trim internal monologue. Shuffle thinker order before passing to Stage 2 to
prevent position bias.

---

## Stage 2 — Sequential Deliberation (Depth)

### Model routing (Niflheim tier)

| Condition | Deliberation model |
|---|---|
| Standard task | `claude-sonnet` |
| High-stakes / K=8 / confidence of best thinker < 0.6 | `claude-opus` (escalate) |

### Deliberation prompt

You are a deliberation agent. You have received a problem and the independent
reasoning attempts of K thinkers. Your task:

    Classify the query type (logical / mathematical / design / open-ended) to
    calibrate your analysis depth.

    Critically evaluate each thinker's reasoning. Do not blindly follow the
    majority — a correct minority view beats an incorrect consensus.

    Identify: agreements, contradictions, and gaps across thinkers.

    If the correct answer appears in at least one thinker: synthesise and
    confirm it with your own reasoning.

    If all thinkers are wrong or insufficient: re-derive the answer
    independently using the problem statement only.

    State your confidence in the final answer: HIGH / MEDIUM / LOW.

Output format:
SYNTHESIS: <your reasoning>
FINAL ANSWER: <answer in the format the task requires>
CONFIDENCE: HIGH | MEDIUM | LOW

### Iterative deliberation (optional)

If CONFIDENCE = LOW after the first deliberation pass, run a second pass:
feed the first deliberation output back into the Memory Cache as an additional
thinker entry, then re-run deliberation once more. Maximum 2 deliberation
passes total.

---

## NOVA Integration

After deliberation completes, write a result shard:

```python
nova_shard_interact(
  message=f"""
  Write shard:
    content: [FINAL ANSWER from deliberation]
    source: heavyskill
    thinker_count: {K}
    deliberation_passes: {N}
    epistemic_state: confirmed   # if CONFIDENCE=HIGH
                    neutral      # if CONFIDENCE=MEDIUM  → flag for NÓTT review
                    contradicted # if CONFIDENCE=LOW     → do not write; escalate
  """
)
```

If CONFIDENCE = LOW: write it with epistemic_state=contradicted so NÓTT can process it anyway, and escalate to the calling orchestrator with the full Memory Cache as evidence.

---

## Integration with forgemaster-orchestrator

When routing a ticket, check the activation predicate before dispatching.
If HeavySkill triggers:

1. Load `forgemaster/skills/forgemaster-heavyskill.md`
2. Run the full pipeline (Stages 1 + 2) in place of a direct single-agent call
3. Substitute the deliberated FINAL ANSWER into the ticket's output slot
4. Add to the ticket record:

heavyskill: true
thinker_count: [K]
deliberation_model: [sonnet | opus]
nova_shard_written: [true | false]

5. Proceed to `forgemaster-verification` as normal

**Never** nest HeavySkill inside a `forgemaster-parallel-lanes` wave — run
HeavySkill first, then parallelise downstream implementation tickets.

---

## Rules

- Never expose the Memory Cache or deliberation meta-analysis to the user
unless they explicitly ask
- Never escalate to `claude-opus` for trivial tasks — the HIGH/K=8 threshold
must be met
- A second deliberation pass is optional, not default — invoke only on LOW
confidence
- HeavySkill is a reasoning hook, not a ticket type — it has no `depends_on`
chain of its own; it inherits from the calling ticket
- Always write the NOVA shard after a successful HIGH or MEDIUM confidence
result — this is how the system learns across sessions
- Thinkers are permitted to read NOVA shards if needed, but must not execute tool calls that cause side effects.
