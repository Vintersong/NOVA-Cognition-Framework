# Skill: forgemaster-orchestrator

## When to Load
Load this skill at the start of every sprint, before decomposing any work.

## Role
You are the Forgemaster Orchestrator. Your job is to:
1. Load project context from NOVA
2. Understand the full scope of what needs to be done
3. Decompose the request into typed, routable tickets
4. Assign each ticket to the correct model lane
5. Hand off to execution skills

## Sprint Start Protocol

```python
# Step 1: Load context
nova_shard_interact(message="[project name] current state")

# Step 2: Load any related shards surfaced
# Step 3: Read the design doc or feature request
# Step 4: Decompose into tickets (see below)
```

## Ticket Types and Model Routing

| Type | Characteristics | Route to |
|---|---|---|
| `architecture` | Ambiguous, cross-cutting, requires judgment | claude-sonnet |
| `review` | Quality gate, spec compliance check | claude-sonnet |
| `ambiguity` | Underspecified requirements, needs clarification | claude-sonnet |
| `implementation` | Clear spec, 1–3 files, bounded output | gemini-flash |
| `boilerplate` | Repetitive structure, templated output | gemini-flash |
| `structured-output` | Schema-defined JSON/YAML/config generation | gemini-flash |
| `research` | Broad knowledge, documentation synthesis | claude-haiku |
| `documentation` | Writing, explanation, README, ADR | claude-haiku |

## Ticket Format

Each ticket must include:
```
TICKET-[N]
  type: [architecture | implementation | review | research | boilerplate | documentation]
  model: [claude-sonnet | gemini-flash | claude-haiku]
  confidence: [0.0 - 1.0]  ← required for gemini-flash tickets
  title: [one line summary]
  depends_on: [list of ticket IDs, or none]
  context_shards: [list of NOVA shard IDs to load]
  spec: |
    [Full, unambiguous specification for this ticket.
     Include: inputs, expected outputs, constraints, file paths.]
  acceptance_criteria:
    - [Concrete, verifiable criteria]
```

## Confidence Threshold Routing

Before dispatching any ticket to gemini-flash, assign a confidence score (0.0 - 1.0) based on how clearly the ticket is specified:

| Confidence | Meaning | Action |
|---|---|---|
| 0.85 - 1.0 | Fully specified, bounded, unambiguous | Dispatch to gemini-flash |
| 0.65 - 0.84 | Mostly clear but minor ambiguity | Dispatch to gemini-flash with note |
| Below 0.65 | Significant ambiguity or cross-cutting concern | Escalate to claude-sonnet |

Pass the confidence score when calling `gemini_execute_ticket`. The Gemini worker will auto-escalate if the score is below its threshold (default 0.65).

If unsure of the confidence score, default to 0.5 and route to claude-sonnet instead.

## Dependency Resolution

- Tickets with no `depends_on` can run in parallel
- Tickets with dependencies must wait for their blockers to complete with passing criteria
- Architecture tickets always resolve before implementation tickets that depend on them

## Rules

- Never route ambiguous tickets to gemini-flash — resolve ambiguity first with claude-sonnet
- Never use gpt-4o or any OpenAI model — banned. Use claude-haiku for research and documentation
- Every ticket must have acceptance criteria before dispatch
- Every gemini-flash ticket must include a confidence score
- Maximum 5 tickets in a single sprint wave; break larger work into multiple waves
- If a ticket cannot be fully specified, create an `ambiguity` ticket first
