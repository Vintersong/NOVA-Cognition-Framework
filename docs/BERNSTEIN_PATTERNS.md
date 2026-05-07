# Bernstein — Patterns Worth Borrowing

Reference notes extracted before deleting `Donors/new/bernstein/` (April 2026).
Upstream: `github.com/chernistry/bernstein` — PyPI `bernstein`, npm `bernstein-orchestrator`.

Bernstein is a direct peer of Forgemaster (multi-agent orchestration for CLI coding agents). Architecture diverges enough that several patterns are worth considering for NOVA/Forgemaster.

---

## 1. Deterministic Python scheduler, zero LLM tokens on scheduling
**Upstream path:** `src/bernstein/core/orchestration/` (manager, tick pipeline, bootstrap, drain, shutdown)

Orchestrator is pure code — no LLM in the scheduling loop. LLMs run only inside short-lived workers. Forgemaster's orchestrator is itself an LLM skill, which costs tokens per sprint and makes routing non-deterministic. Worth evaluating a pure-code dispatcher that only calls LLMs for the ambiguous cases.

## 2. Uniform adapter layer over 18 CLI agents
**Upstream path:** `src/bernstein/adapters/` (claude, codex, gemini, qwen, aider, amp, roo_code, cursor, cody, continue_dev, goose, iac, kilo, kiro, ollama, opencode, tabby, generic)

Each adapter exposes the same interface over a different CLI. Forgemaster currently routes only Claude + Gemini. An adapter pattern would unlock Codex CLI, Aider, local models (Ollama), Goose, and a generic `--prompt` shim for anything else.

## 3. File-based state with WAL crash recovery
**Upstream path:** `src/bernstein/core/persistence/` + `.sdd/` workspace

WAL + checkpointing for sprint/task state. NOVA has shard files and session flush, but no WAL or mid-sprint crash recovery. Useful when sprints are long enough that a crash mid-run loses real work.

## 4. Cost and token monitoring with auto-intervention
**Upstream paths:** `src/bernstein/core/cost/`, `src/bernstein/core/tokens/`

Budget enforcement, anomaly detection, growth-driven auto-intervention (pauses runaway agents). NOVA has `nova_usage.jsonl` as passive log only — no active budget layer. Pairs naturally with the emotional-state-routing bullet in ROADMAP.

## 5. Contextual bandit router
**Upstream path:** `src/bernstein/core/routing/`

Learns model/effort selection from outcomes instead of a static task→model table. Forgemaster's `AGENTS.md` routing table is hand-tuned; a bandit would adapt per-project.

## 6. Role templates Forgemaster doesn't have cleanly
**Upstream path:** `templates/roles/`

Roles present in Bernstein but absent or weakly covered in `forgemaster/agents/`: `ci-fixer`, `resolver`, `visionary`, `analyst`, `retrieval`, `prompt-engineer`. Worth reviewing whether any should be added.

## 7. Plan YAML with stages + depends_on, deterministic task injection
**Upstream paths:** `templates/plan.yaml`, `src/bernstein/core/planning/`

`stages` with `depends_on: [stage_name]`, `steps` with `goal/role/priority/scope/complexity`. Running a plan file skips LLM decomposition entirely — deterministic injection into the task queue. Alternative to `forgemaster-writing-plans` for projects where the decomposition is already known.

---

## Operational principles worth quoting directly

From Bernstein's `CLAUDE.md`:

- *Bernstein orchestrates SHORT-LIVED agents (1-3 tasks each, then exit).*
- *State lives in FILES, not in agent memory.*
- *Agents are spawned fresh per task — no "sleep" problem.*
- *Model and effort are chosen per-task based on complexity.*
- *The orchestrator is DETERMINISTIC CODE, not an LLM — no LLM-based scheduling.*

These are directly compatible with NOVA's shard-as-state philosophy. If Forgemaster moves toward a pure-code orchestrator (item 1), these become the governing principles.
