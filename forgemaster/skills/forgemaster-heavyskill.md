@@verification: unverified
@@capabilities: *

# Skill: forgemaster-heavyskill

## When to Load

Load this skill when the current task is **hard, verifiable reasoning**:

- Mathematical / STEM problems with numeric or logical answers
- Algorithmic / competitive-programming style problems
- Multi-constraint design or deduction where correctness is objectively checkable (tests, rubric, explicit constraints)
- Cases where the primary agent is uncertain about its initial approach, or prior attempts produced inconsistent answers

**Do NOT load** for:

- Simple factual lookups or pure information retrieval
- Casual conversation, style coaching, or preference-based work
- Straightforward code edits with obvious local fixes
- Subjective tasks without objective correctness criteria

This skill is a **reasoning hook**, not a ticket type. It wraps a single hard sub-problem inside an existing workflow; the surrounding sprint continues normally afterward.

## Role

You are the HeavySkill orchestrator. Your job is to:

1. Extract the core reasoning problem from the request
2. Run **Stage 1 — Parallel Reasoning** (K=3 independent Haiku thinkers)
3. Serialize their outputs into a memory cache
4. Run **Stage 2 — Sequential Deliberation** yourself (do not delegate Stage 2)
5. Write a NOVA shard recording the result
6. Return only the final answer to the caller — no meta-analysis surfaces

## Stage 1 — Parallel Reasoning Protocol

### Spawn K=3 Haiku thinkers

Use the `Agent` tool with `model: haiku`, `subagent_type: general-purpose`. Dispatch **all three calls in a single message** so they run in parallel. Each thinker receives **only** the problem statement — no other context, no awareness of the other thinkers.

Per-thinker prompt:

```
Solve the following problem step by step.
Show your complete reasoning and arrive at a final answer.

Problem:
<problem text>

Think carefully and solve this independently. Show all work, then state
your final answer on a line beginning with "Answer:".
```

### Independence rules

- Thinkers must not share context, memory, or each other's intermediate output.
- Each must produce a full chain of thought **and** a final answer — not just an answer.
- Do not pre-bias them with hints from the orchestrator's own reasoning. If a strategy hint is needed for diversity, vary it across thinkers (e.g. one algebraic, one constructive, one brute-force).

### K is fixed at 3

K=3 is the interactive default. Do not raise K without explicit user request — higher K multiplies cost without proportional gains in this harness.

## Memory Cache Serialization

Concatenate the three thinker outputs into a single cache string using this exact template:

```
Here is a problem, and multiple thinkers try to give their thought processes independently.
====== Problem ======
<problem text>
==== Thinkers Process Start ====
# ----- Thinker #1 -----
<thinker 1 reasoning and final answer>
# ----- Thinker #2 -----
<thinker 2 reasoning and final answer>
# ----- Thinker #3 -----
<thinker 3 reasoning and final answer>
==== Thinkers Process End ====
```

### Rules

- **Shuffle** the order of thinkers before insertion to avoid position bias. The numbering above is positional, not identity.
- **Prune** if the cache would overflow the deliberator's context: keep each thinker's final reasoning chunk and final answer; drop early scratch work first.
- Do not edit, summarize, or "clean up" any thinker's reasoning — pass it through verbatim (modulo pruning).

## Stage 2 — Sequential Deliberation Protocol

The orchestrator (you, running as Sonnet) performs deliberation directly. Do not spawn a subagent for this step — keeping it in the main thread preserves quality and avoids needless serialization.

### Deliberation prompt (run in-context)

Treat the memory cache as user input and reason against it with this directive:

```
You are seeing multiple independent thought processes for the same problem.
Your job is meta-analysis and synthesis, NOT to solve the problem like
another thinker.

1. Summarize each thinker's approach in one line.
2. Identify logical errors, gaps, or unjustified leaps in each.
3. Decide which reasoning path(s) — if any — are sound.
4. If all thinkers are wrong, learn from their mistakes and re-reason
   the problem from scratch.
5. Produce ONE final answer in the correct domain format
   (e.g. boxed expression for math, fenced code block for code).
```

### Output constraints

- The user-visible reply contains **only the final answer**, formatted for the domain.
- The meta-analysis (steps 1–4 above) is internal scratch — do not surface it unless the user explicitly asks for the deliberation trace.
- If deliberation cannot reach a confident answer, return the best candidate **and** an explicit uncertainty note. Do not fabricate certainty.

### Iterative deliberation

**Deferred.** v1 runs exactly one deliberation pass. Do not append the deliberation's own reasoning back into the cache for a second round.

## NOVA Write (mandatory)

After producing the final answer, write a shard. This is not optional — every HeavySkill invocation produces a shard so confidence and provenance can be tracked over time.

```python
nova_shard_create(
    guiding_question="<original problem statement>",
    intent="reflection",
    theme="heavyskill",
    source="agent_inference",
    initial_message="""
HEAVYSKILL_RUN
K: 3
thinker_model: claude-haiku-4-5-20251001
deliberator_model: claude-sonnet-4-6
deliberation_passes: 1
confidence: <high | medium | low>

FINAL_ANSWER:
<final answer>

THINKER_ANSWERS:
- Thinker A: <one-line answer>
- Thinker B: <one-line answer>
- Thinker C: <one-line answer>

DELIBERATION_NOTES:
<2–4 sentence rationale: which thinker(s) were correct, or why re-reasoning was needed>
""".strip(),
    related_shards="<triggering shard id, if any>",
    relation_type="derived_from",
)
```

### Confidence rubric

- **high** — at least two thinkers converged on the final answer and deliberation found no errors in their reasoning.
- **medium** — one thinker was correct, or deliberation re-reasoned from a partial trajectory.
- **low** — all thinkers were wrong and deliberation produced a fresh answer; flag for verification before downstream use.

If a triggering project shard is known, set `related_shards` to its id so the HeavySkill shard is reachable via `nova_graph_query(target=<project_shard>, relation_type="derived_from")`.

## Execution Summary

1. Detect activation predicate → load this skill.
2. Spawn 3 Haiku thinkers in parallel (single message, three `Agent` calls).
3. Collect their outputs; build the shuffled, pruned memory cache.
4. Run deliberation in-context as Sonnet; produce one final answer.
5. `nova_shard_create(...)` per the template above.
6. Return only the final answer to the calling workflow.

Hand control back to the surrounding skill (typically `forgemaster-implementation` or `forgemaster-systematic-debugging`) for verification and downstream use.
