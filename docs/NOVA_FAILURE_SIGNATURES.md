# NOVA Failure Signatures

A catalog of NOVA / Forgemaster failure modes, their observable symptoms,
likely causes, and corrective actions. Structured after §5.2 of Srinivasan,
*A Methodology for Selecting and Composing Runtime Architecture Patterns
for Production LLM Agents* (arXiv 2605.20173), adapted to the patterns
NOVA actually uses.

Each signature has three parts:

- **Symptom** — what a reader can match against logs (`nova_usage.jsonl`,
  `audit_log.py` output, `output/forgemaster_runs/*.jsonl`).
- **Cause** — the most common underlying mechanism.
- **Correction** — the concrete fix, ranked by reversibility (cheap first).

The catalog is not closed. Add entries when a new failure mode is diagnosed
and the correction generalises.

---

## Retrieval (HUGINN / MUNINN)

### Replay divergence on retrieval
- **Symptom.** `nova_calibrate_routing(include_replay_divergence=True)`
  reports `divergence_rate >= 0.20`. The same query hash returns different
  top-1 shards across runs.
- **Cause.** Confidence decay (`NOVA_DECAY_RATE`), HUGINN model version
  rotation, or new shards entering the corpus shift the ranking between
  calls. This is the P3-shaped failure: an LLM consumer of a non-stationary
  store gives inconsistent answers under "replay".
- **Correction.**
  - Pin `HUGINN_MODEL` to a specific version for any workload that requires
    determinism across calls.
  - Reduce `NOVA_DECAY_RATE` if temporal drift is the dominant factor.
  - For workflows that need strict consistency, materialise the top-1
    result into a shard (state-machine spine) instead of recomputing it.

### HUGINN silently falling back to local embeddings
- **Symptom.** Retrieval still returns answers but `huginn_confidence` is
  consistently low; quality of top-k drops; cold cache.
- **Cause.** `CLAUDE_API_KEY` not set or rate-limited; ravens.py falls back
  to local `all-MiniLM-L6-v2` without raising.
- **Correction.** Check the startup warning in the server log. Verify
  `CLAUDE_API_KEY` is loaded from `.env`. The fallback is intentional but
  should be visible — `nova_calibrate_routing` exposes the suggested
  threshold so the operator can see consistency dropping.

### MUNINN timeout cascading into HUGINN-only retrieval
- **Symptom.** `muninn_used: false` returned in nearly every interact call,
  even on low-confidence HUGINN results.
- **Cause.** Sonnet API latency exceeds the 20s `asyncio.wait_for` budget
  in `shard_tools.nova_shard_interact`.
- **Correction.** Inspect Anthropic status; raise the timeout if latency
  is consistently high; consider a smaller MUNINN model.

---

## Shard write path (the SDB commit step)

### Quarantine traps newly extracted shards
- **Symptom.** A shard created via `session_extracted` source disappears
  from `nova_shard_search` results immediately after creation. State query
  shows `quarantine_until` in the future.
- **Cause.** By design — `QUARANTINE_HOURS` blocks recently-extracted shards
  from retrieval until human review.
- **Correction.** Either wait for the quarantine to elapse, or surface the
  shard via `nova_shard_query_state(keyword=..., min_confidence=0)` for
  review. The new reject contract in `mcp/reject.py` returns
  `RejectCode.QUARANTINED` with `retryable=True`.

### Compaction loses high-signal turns
- **Symptom.** A `nova_shard_get_full` after auto-compaction (at
  `NOVA_COMPACT_THRESHOLD=30` turns) shows a shorter body than expected;
  important context missing.
- **Cause.** `maintenance.compact_shard` summarises older turns; if the
  summary model collapses load-bearing details, they're gone.
- **Correction.** Raise `NOVA_COMPACT_THRESHOLD` for high-value shards.
  Tag critical decisions in the `guiding_question` so compaction preserves
  them. Run `nova_shard_consolidate(dry_run=True)` to inspect the last
  compaction report before tuning.

### Merge collapses contradictory shards into a meta-shard
- **Symptom.** After `nova_shard_merge`, the meta-shard's conversation
  history contains turns whose conclusions contradict each other; later
  retrieval is incoherent.
- **Cause.** Merge concatenates and sorts by timestamp. It does not detect
  semantic contradiction. The `contradicts` relation in the graph is
  enforced at *create*, not at *merge*.
- **Correction.** Before merging, query the graph for `relation_type=contradicts`
  edges among the candidate shard IDs. If any exist, supersede the older
  side and only merge the survivors. Do not merge contradicting shards.

### Confidence-decay cascade hides corroborated shards
- **Symptom.** Shards that were once high-confidence drop below
  `NOVA_CONFIDENCE_LOW` and get tagged `low_confidence`, silently excluded
  from default search.
- **Cause.** `NOVA_DECAY_RATE` (default 0.05 per 7d) compounds. A shard
  unused for ~14 weeks crosses the 0.4 floor unless re-corroborated.
- **Correction.** Use `include_low_confidence=True` to recall deliberately,
  then `nova_shard_update` (which resets `last_used`) to re-corroborate.
  For shards that should never decay, supersede with a "permanent fact"
  shard tagged accordingly.

---

## Forgemaster sprint pipeline

### Reviewer rubber-stamps implementer output
- **Symptom.** Sprint pass rate (`nova_calibrate_routing` →
  `forgemaster.routing_stats`) approaches 100% for a given
  `(task_type, model)` pair, but downstream sprints built on those outputs
  fail.
- **Cause.** The reviewer skill is prompted in a way that biases toward
  PASS; or the reviewer and implementer share the same model and converge.
- **Correction.** Force the reviewer onto a different model than the
  implementer (Sonnet review of Gemini implementation is the intended
  pairing). Tighten the reviewer's PASS criteria. The pass rate falling
  back to the 60–80% band is the healthy signal.

### Empirical routing locks onto a bad model
- **Symptom.** `route_ticket` returns the same model for a task_type even
  after multiple recent failures.
- **Cause.** `_EMPIRICAL_MIN_SAMPLES=10` floor: once a (task_type, model)
  pair accumulates >=10 wins, it dominates until enough new failures
  reverse it.
- **Correction.** Clear stale event-log entries or rotate the
  `output/forgemaster_runs/` directory. Lower
  `NOVA_EMPIRICAL_CACHE_TTL_S` so the empirical table reloads sooner.
  Worst case: bypass the routing table with a complexity-keyword override.

### Biconditional audit check fails after a sprint
- **Symptom.** Sprint outcome event `biconditional_failed` appears with
  `unaccounted_changes` or `phantom_records` populated.
- **Cause.** A shard was modified outside the audit log path, or an audit
  record exists for a write that didn't actually land on disk. The two
  are equally serious — they break the SDB commit step.
- **Correction.** Treat as a P1 incident. Inspect both sets:
  - `unaccounted_changes` — shards changed without an audit record.
    Someone wrote outside the gate. Trace via `nova_usage.jsonl`.
  - `phantom_records` — audit records without a corresponding disk change.
    Check for crashed writes, disk-full, or partial atomic_io rename
    failures.

### Capability gate denies a write the user expects
- **Symptom.** `RejectCode.GATE_DENIED` returned from
  `nova_shard_archive` / `nova_shard_forget` / `nova_shard_consolidate`
  / `nova_forgemaster_sprint`.
- **Cause.** Active skill lacks `fs.write.irrev` or another required
  capability tag in its `@@capabilities` frontmatter.
- **Correction.** Either grant the capability in the skill manifest, or
  invoke the tool from a skill that does. The reject envelope's `hint`
  field points to the audit log entry with the exact missing tag.

---

## Hook system

### Hook fires on the wrong event
- **Symptom.** Behaviour expected on `SESSION_START` (e.g. light decay)
  instead runs on `POST_SPRINT` and saturates.
- **Cause.** Hook registration order in `hooks.py` got reshuffled, or two
  hooks registered for the same event name from different modules.
- **Correction.** `hooks.py` is the single registration point. Audit
  `NovaHookEvent` callers with `grep emit\(NovaHookEvent`. There should
  be one emit per logical event.

### Pre-compact hook never runs
- **Symptom.** Shards exceed `NOVA_COMPACT_THRESHOLD` but no compaction
  attempt is logged.
- **Cause.** `nova_hook_precompact` import failed silently at server boot;
  hooks bus has the event slot but no handler.
- **Correction.** Check server startup log for import errors. Re-register
  via `register_default_hooks(...)` in `server_context.bootstrap`.

---

## Operations that are NOT failures

These look like failures but are by design — do not "fix" them.

- A shard returned via `include_low_confidence=True` having
  `confidence < 0.4`. That's the requested behaviour.
- A `nova_shard_forget` call leaving the file on disk. Forget is a
  soft-delete; provenance preservation is intentional.
- HUGINN returning identical results for two paraphrased queries. Embedding
  similarity is the point.
- `nova_calibrate_routing` reporting `suggested_threshold == current` with
  delta `0.000`. It means routing is already tuned; no action.

---

## Reading this catalog

If you've matched a symptom to an entry above, the correction is the
starting point, not the answer. Production incidents in NOVA almost always
cross at least two of these signatures (a retrieval drift triggers a merge
collapse triggers a biconditional failure). Diagnose forwards from the
earliest symptom in the trace.

If your symptom matches nothing here, write the new entry first, then fix
the bug. The catalog is the artifact; the fix is the consequence.
