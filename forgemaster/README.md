# Forgemaster

Multi-agent orchestration layer for the NOVA Cognition Framework. Forgemaster decomposes design docs into tickets, routes each to the optimal LLM lane (Sonnet / Gemini Flash / Haiku), executes them in parallel, and writes decisions back to NOVA as persistent memory.

Forgemaster does not think for you — it scaffolds how specialized models collaborate on a sprint, with NOVA as the shared context across sessions.

## Layout

| Path | Contents |
|---|---|
| `AGENTS.md` | Orchestration config: agent roles, model routing, tool allowlist |
| `STANDARDS.md` | Authoring standard for every skill, agent, and template |
| `SKILL_LIBRARY.md` | Master index of ~200 skills across 15 domains |
| `skills/` | 10 core orchestration skills (always loaded first) |
| `library/` | Domain skill library (15 categories) — load per-ticket |
| `agents/` | 18 divisions, ~319 agent persona definitions (see `agents/README.md`) |
| `templates/` | Starters for CLAUDE.md, agents, skills |
| `docs/` | Internal reference and operating notes |

## Core workflow

```
1. nova_shard_interact               load project context from NOVA
2. forgemaster-writing-plans         decompose design doc into tickets
3. forgemaster-orchestrator          classify tickets by type
4. forgemaster-parallel-lanes        dispatch independent tickets concurrently
5. forgemaster-code-review           two-stage review (spec + quality)
6. forgemaster-qa-review             stage 3 structural QA
7. nova_shard_update                 write decisions back
```

Every 3 sprints: `nova_shard_consolidate`.

## Model routing (from `AGENTS.md`)

| Task | Model |
|---|---|
| Architecture, ambiguous requirements, code review | `claude-sonnet` |
| Clear spec, 1–3 files, boilerplate, structured output | `gemini-flash` |
| Research, documentation, fast-read tasks | `claude-haiku` |
| UI / screen design | `stitch` |

OpenAI models are banned — see `forgemaster-orchestrator` for the rule.

## Getting started

1. Read the root `CLAUDE.md` for NOVA + Forgemaster session setup.
2. Read `skills/forgemaster-orchestrator.md` before routing any ticket.
3. Consult `SKILL_LIBRARY.md` when a task falls outside the core skills.
4. Follow `STANDARDS.md` when authoring anything new.

## Relationship to NOVA

Forgemaster is the orchestration surface. NOVA is the memory substrate. Never skip the `nova_shard_interact` → sprint → `nova_shard_update` loop — that's what keeps context coherent across sessions.
