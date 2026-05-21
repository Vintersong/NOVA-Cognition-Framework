# Srinivasan SDB Methodology — NOVA Comparison & Picks

**Source paper.** Vasundra Srinivasan, *A Methodology for Selecting and
Composing Runtime Architecture Patterns for Production LLM Agents*,
arXiv 2605.20173v1, May 2026.

**Companion repo.** https://github.com/vasundras/agent-runtime-patterns

**Reviewed.** 2026-05-21 against NOVA + Forgemaster (this repo).
**Branch.** `claude/compare-nova-alternative-hNKv7`.

---

## Paper's thesis in one paragraph

The load-bearing primitive of every production agent runtime is the
*stochastic-deterministic boundary* (SDB) — a four-part contract among
**proposer** (the LLM), **verifier** (deterministic check), **commit**
(durable write), and **reject** (typed response back when verification
fails). Around it: three concerns (Coordination / State / Control) and
six patterns (P1 Hierarchical Delegation, P2 Scatter-Gather + Saga, P3
Event-Driven Sequencing, P4 Supervisor + Gate, P5 Shared State Machine,
P6 Human in the Loop). The reliability decomposition `y(t) = µt + σξ(t)`
argues that as per-call variance σ shrinks across model generations,
*architectural momentum* µ — set by pattern choice — becomes the dominant
lever on long-run reliability.

## How NOVA maps

| SDB part | NOVA implementation | Strength |
|---|---|---|
| Proposer | Forgemaster lanes (Sonnet/Haiku/Gemini) | strong |
| Verifier | `capability_gate.py`, `schemas.py` (Pydantic), `skill_verification.py` | strong |
| Commit | `atomic_io.py`, `store.py`, `nova_shard_db.py` | strong |
| Reject | Was: free-text `{"status":"error","message":...}` everywhere | **weak — addressed in this branch** |

| Concern | NOVA pattern fit |
|---|---|
| Coordination | `forgemaster-orchestrator` = P1; `forgemaster-parallel-lanes` ≈ P2 (no explicit saga compensation) |
| State | Hybrid P3/P5 — shards are JSON files (P3) with confidence decay + merge (P5-flavoured). Plus session_store, graph, wiki, facts SQLite |
| Control | `audit_log.py` + `capability_gate.py` = P4. **No P6** primitive (no kill switch / approval queue / throttle plane) |

## What this branch implements

Four picks from the comparison were judged high-return / low-risk:

1. **Typed reject contract** — `mcp/reject.py` (new). Replaces ad-hoc
   `{"status":"error","message":...}` with a typed envelope:
   ```json
   {
     "status": "rejected",
     "code": "shard_not_found",
     "message": "...",
     "retryable": false,
     "hint": "Use nova_shard_search to find the right shard_id.",
     "target": "<shard_id>"
   }
   ```
   Wired into the not-found paths of `nova_shard_update`,
   `nova_shard_get`, `nova_shard_get_full`, `nova_shard_merge`,
   `nova_shard_archive`, `nova_shard_forget`. Other tools can adopt
   incrementally — `RejectCode` enum holds the canonical codes.

2. **Replay-divergence diagnostic** — `_compute_replay_divergence` in
   `mcp/calibrate.py`. For queries that appear `>= k_runs` times in
   the HUGINN log, computes how often the top-1 shard changed across
   runs. Surfaces as `result["replay_divergence"]` in
   `nova_calibrate_routing` output. The `>= 20%` band triggers a
   pin-model / lower-decay / spine-migration recommendation, matching
   the paper's P3 → P5 migration trigger (§5.2).

3. **`runtime_class` on sprints** — `ForgemasterSprintInput` now
   accepts `task_type` and `runtime_class` (literal:
   `conversational | autonomous | long_horizon | auto`). Threaded
   through `ForgemasterRuntime.run_sprint`. Recorded on the routing
   and sprint_verdict JSONL events. Surfaced in the sprint return
   payload. Empirical routing (in calibrate.py) can group on these in
   future without schema migration.

4. **`docs/NOVA_FAILURE_SIGNATURES.md`** — NOVA-specific failure-
   signature catalog mirroring paper §5.2. Covers retrieval drift,
   write-path failures, sprint-pipeline failures, hook failures, and
   the operations that look like failures but aren't.

## Bonus fix (pre-existing bug)

`mcp/calibrate.py` referenced `CalibrateRoutingInput` from `schemas.py`
but the schema did not exist. The module was also never registered in
`nova_server.py`. Both fixed:

- `CalibrateRoutingInput` added to `schemas.py`.
- `register_calibrate_tools(mcp)` called from `nova_server.py`.

The tool `nova_calibrate_routing` is now reachable over MCP.

## What was NOT imported (and why)

- **Pure P3-vs-P5 spine choice.** NOVA's hybrid (shards as files + SQLite
  index + graph) is justified by its workload (semantic recall, not
  workflow execution). Forcing a single spine would be a regression.
- **Saga compensation (P2).** NOVA writes are idempotent JSON edits;
  there are no external side-effects worth compensating.
- **Console-first as gospel.** `nova_usage.jsonl` + audit log + graph
  are already the operator console. A UI before more features inverts
  the value.
- **5-step ADR per sprint.** Sprints are 4 turns; mandatory ADR would
  smother the velocity. Apply it at project boundaries instead.
- **The `µt + σξ(t)` model as anything but metaphor.** The paper says
  so explicitly. Don't build calibration that pretends to estimate µ.

## Where to start a follow-up

If this proves useful in practice, the next picks are:

- Map remaining `{"status": "error"}` returns across `wiki_tools.py`,
  `graph_tools.py`, `forgemaster_tools.py`, `nidhogg.py` to typed
  reject codes.
- Add a `P6` primitive: cancellation token threaded through long-running
  tools (`nova_shard_consolidate`, `nidhogg_scan`), plus a per-tenant
  throttle in `permissions.py`.
- Quarterly cron entry: `nova_calibrate_routing` with replay divergence
  → write a calibration shard with the trend.
