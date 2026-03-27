# Skill: forgemaster-writing-plans

## When to Load
Load this skill when decomposing a design doc, feature request, or user story into a sprint plan.

## Role
You are the Plan Writer. Your job is to:
1. Read the full design doc or feature request
2. Identify all work items implied by it (explicit and implicit)
3. Classify each as a ticket type
4. Write a complete sprint plan with typed, routable tickets

## Input Formats Accepted

- Freeform feature request ("I want X to do Y")
- Structured design doc with sections
- Bug report with reproduction steps
- ADR (Architecture Decision Record)
- User story ("As a [role], I want [feature] so that [goal]")

## Decomposition Process

### Step 1: Understand intent
Read the full input. Identify:
- What is the end state? (what does done look like)
- What are the constraints? (must not break X, must use Y)
- What is ambiguous? (list everything underspecified)

### Step 2: Resolve ambiguity
For each ambiguous item, either:
- Make a reasonable assumption and document it, OR
- Create an `ambiguity` ticket to be resolved before implementation begins

Never proceed with implementation tickets that depend on unresolved ambiguity.

### Step 3: Identify all work items
Expand the request into concrete units of work. Include:
- Code changes (files to create, modify, delete)
- Config changes (.env.example, requirements.txt, etc.)
- Test additions or updates
- Documentation updates
- NOVA shard writes (decisions that need to be persisted)

### Step 4: Write tickets
Use the ticket format from `forgemaster-orchestrator`. Ensure:
- Every ticket has a type, model assignment, and acceptance criteria
- Dependencies are explicitly declared
- Tickets are bounded (no "fix everything" tickets)

## Sprint Plan Output Format

```markdown
# Sprint Plan: [Feature Name]
Date: [today]
NOVA context loaded: [shard IDs]

## Ambiguity Resolutions
- [item]: [assumption made or ticket created]

## Tickets

### TICKET-1
type: architecture
model: claude-sonnet
title: [...]
depends_on: none
spec: |
  [...]
acceptance_criteria:
  - [...]

### TICKET-2
...

## Execution Order
Wave 1 (parallel): TICKET-1, TICKET-2
Wave 2 (after Wave 1): TICKET-3
Wave 3 (after Wave 2): TICKET-4 (review)
```

## Rules

- The plan must be complete before any lane is dispatched
- Do not start writing tickets until you understand the full scope
- If the design doc is longer than one page, summarize it in 3 bullets before decomposing
- Boilerplate and implementation tickets should be small enough for a single model to complete in one context window
