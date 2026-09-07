# nott.py

**One-line purpose:** NÓTT — the maintenance daemon. Runs decay, compaction, merge suggestions, and graph sync in response to hook events.

## Why it exists

Maintenance work (confidence decay, shard compaction, merge detection, graph sync) is expensive and should never block user-facing tool responses. NÓTT encapsulates all four maintenance passes behind a single `run(trigger, dry_run)` entry point, running the right subset of work based on the trigger level. Fire-and-forget invocation via `asyncio.create_task` is the contract; `emit_wait` is reserved for explicit user requests (`nova_shard_consolidate`).

## Key concepts

- **`NottTrigger`** enum — four levels:
  - `SESSION_START` — decay pass only (every `nova_shard_interact`)
  - `COUNT_THRESHOLD` — decay + merge suggestions (when shard count exceeds threshold)
  - `POST_SPRINT` — full cycle: decay + compact + merge + graph sync (after `nova_shard_update`)
  - `SCHEDULED` — same as `POST_SPRINT`; for `nova_shard_consolidate`
- **`NottReport`** — backwards-compatible with the old `nova_shard_consolidate` response shape: `decayed_shards`, `compacted_shards`, `merge_suggestions`, `total_shards`, `summary`.
- **Dependency injection** — all maintenance functions are passed as callables at construction (`decay_fn`, `compact_fn`, `merge_fn`, etc.). This avoids circular imports with `nova_server.py` and makes NÓTT independently testable.
- **Dedicated thread pool** — `ThreadPoolExecutor(max_workers=2, thread_name_prefix="nott")` runs blocking I/O (shard file reads/writes) without contending with the default asyncio executor used by ravens retrieval.
- **`SILENT_MARKER`** — `SESSION_START` cycles that found nothing to decay set `report.silent = True`, suppressing the usage log write. Borrowed from hermes-agent `cron/scheduler.py`.
- **`_pre_compact`** — optional hook called before each shard is compacted; intended for fact extraction before turns are summarised away. Not wired to any concrete function currently.

## Public surface

- `Nott.__init__(shard_dir, graph_file, usage_log_file, ...<callables>)` — constructor; all maintenance functions injected.
- `Nott.run(trigger, dry_run) → NottReport` (async) — main entry point.

## Inputs and outputs

- **Reads/writes:** delegated entirely to injected callables. NÓTT itself never opens files.
- **Writes:** `nova_usage.jsonl` via `_log` (except silent cycles).
- **Env:** none consumed directly.

## Invariants and assumptions

- `_graph_sync` reads `confidence` from the index (float) and writes it back to graph entities — same float confidence assumption as everywhere else. Must be updated in the discrete confidence migration.
- The `_decay_pass_sync`, `_compact_pass_sync`, and `_merge_pass_sync` methods all run in the `_executor` thread pool — they are not async themselves, just offloaded.
- `_merge_pass_sync` only checks shards tagged `"enriched"` — unenriched shards are never considered for merge.
- Merge candidate deduplication uses `tuple(sorted([shard_a, shard_b]))` as the pair key — each pair is surfaced at most once.

## Callers and integration

- `server_context.py` — constructs the NÓTT singleton and registers its lambdas with the hook registry for `SESSION_START`, `POST_SPRINT` and `COUNT_THRESHOLD`.
- No other callers outside `mcp/`.

## Known gaps / open questions

- `_pre_compact` is wired in the constructor signature but no concrete function is passed from `ServerContext.bootstrap()` — fact extraction before compaction is not implemented.
- `_graph_sync` stores float confidence values from the index — blocked by the same discrete confidence migration as `maintenance.py`.
- NÓTT's merge suggestions are surfaced in the report but there is no automatic action taken — a human must act on them via `nova_shard_merge`. The suggestions cap at 10 in `to_dict()`.
