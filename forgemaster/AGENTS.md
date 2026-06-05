---
name: forgemaster
description: >
  Multi-agent orchestration system using NOVA as persistent memory backplane.
  Routes tasks to specialized LLM lanes based on task type.
  Human touchpoints: design doc, plan approval, PR review.
agent_roles:
  - orchestrator
  - planner
  - implementer
  - reviewer
  - qa
  - researcher
preferred_models:
  claude-sonnet: [architecture, review, ambiguity]
  gemini-flash: [implementation, boilerplate, structured-output]
  claude-haiku: [research, documentation, fast-tasks]
tools_allowed:
  - bash
  - git
  - mcp_nova
  - mcp_gemini
  - mcp_stitch  # pending — auth blocked, see KNOWN_ISSUES.md
context_files:
  - forgemaster/AGENTS.md
  - forgemaster/skills/forgemaster-orchestrator.md
max_context_usage: 0.5
verification_priority: high
---
# Forgemaster
Multi-agent orchestration layer built on NOVA cognitive memory.

## How It Works
1. You provide a design doc or feature request
2. Orchestrator loads relevant NOVA shards for project context
3. Orchestrator decomposes into typed tickets
4. Each ticket routes to the optimal model based on task type
5. Agents execute in parallel sandboxed lanes
6. Results return as PRs for human review
7. Orchestrator writes decisions back to NOVA

## Task Types and Model Routing

| Task | Model |
|---|---|
| Architecture decisions, ambiguous requirements | claude-sonnet |
| Clear spec, 1-3 files, structured output | gemini-flash |
| Research, documentation, broad knowledge | claude-haiku |
| UI/screen design, frontend mockups       | stitch        |
| Boilerplate, formatting, deterministic output | gemini-flash |
| Code review, quality judgment | claude-sonnet |

### Per-role model overrides

The role→model mapping in `mcp/forgemaster_runtime.py:_ROLE_TO_MODEL` resolves through four env vars (`FORGEMASTER_ORCHESTRATOR_MODEL`, `FORGEMASTER_PLANNER_MODEL`, `FORGEMASTER_REVIEWER_MODEL`, `FORGEMASTER_IMPLEMENTER_MODEL`) declared in `mcp/config.py`. Unset, they fall back to `MUNINN_MODEL` (the three Anthropic roles) and `GEMINI_MODEL` (implementer) — i.e. current behavior. Point any role at an Opus alias on high-stakes sprints; the orchestrator and reviewer are the two roles where the cost premium pays for itself most reliably. The HeavySkill stage-2 escalation in `forgemaster/skills/forgemaster-heavyskill.md` is a separate, complementary mechanism — it applies inside K-thinker deliberation, not at the sprint role level.

## NOVA Integration
Every sprint starts with: nova_shard_interact (load project context)
Every sprint ends with: nova_shard_update (write decisions made)
Every 3 sprints: nova_shard_consolidate (maintain memory health)

## Skill Library
The full skill library is indexed at `forgemaster/SKILL_LIBRARY.md`.
It covers 25 specialization domains with 218 skills across the workspace.
Always consult it when a task falls outside Forgemaster's core domain.

## Repository Layout — Personas vs. Reference Material

`forgemaster/agents/` mixes two file types:

- **Persona agents (221)** — `.md` files with a `name:` frontmatter field. These are dispatchable personas across 18 divisions.
- **Reference / runbook / example / engine-docs files (~99)** — supporting material (e.g. `engine-reference/`, `examples/`, `strategy/` runbooks, `research/program.md`) co-located with personas.

A future pass will relocate the reference material into `forgemaster/resources/` so that `agents/` holds only true personas. Three frontmatter schemas are currently in active use (base / metadata-extended / technical-extended); a unified schema will be drafted in the same pass. See `docs/AUDIT-2026-05-12.md` and `docs/AUDIT-2026-05-12-followup.md`.

Core skills (load first for internal operations):
- forgemaster-orchestrator: sprint planning and routing
- forgemaster-parallel-lanes: dispatch to agent sandboxes
- forgemaster-writing-plans: decompose design docs into tickets
- forgemaster-implementation: single ticket execution
- forgemaster-systematic-debugging: root cause investigation
- forgemaster-verification: evidence before completion claims
- forgemaster-git-workflow: branch setup and integration
- forgemaster-code-review: two-stage spec then quality review
- forgemaster-qa-review: stage 3 structural QA (thresholds, anti-patterns, JIRA)
- forgemaster-nova-session-handoff: persist state across sessions

Extended domains (see SKILL_LIBRARY.md for paths):
- Agentic Workflows: brainstorming, TDD, parallel agents, plan execution
- Engineering: Python, TypeScript, Go, Rust, React, Next.js, GraphQL, Rust, WASM
- Infrastructure & DevOps: Docker, K8s, Terraform, Ansible, AWS/Azure/GCP, CI/CD
- Data/AI/ML: ML engineering, LLM fine-tuning, Hugging Face full suite
- Databases: PostgreSQL, MongoDB, Redis, Elasticsearch, SQL
- Security: OWASP audit, OAuth, cryptography, compliance
- Frontend & Design: Impeccable (20 refinement skills), UI/UX Pro Max, design systems
- Game Development: Full studio pipeline (35 skills, Claude-Code-Game-Studios)
- Project Management: Jira, Linear, Notion, Confluence, Agile
- Code Intelligence: GitNexus (impact analysis, PR review, refactoring)
- Autonomous Agents: Browser, Researcher, Collector, Lead Gen, Predictor, Trader
- Communication: Technical writing, email, presentation, PDF
- Observability: Prometheus, Sentry, Slack
