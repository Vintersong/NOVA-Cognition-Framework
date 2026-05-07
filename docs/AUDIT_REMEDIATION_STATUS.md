# NOVA Audit Remediation — Status & Roadmap

**Branch:** `pr1-stability-and-dormant-features`
**Last update:** 2026-05-07
**Audit base commit:** `e6b6738` (baseline of in-flight runtime + library work)

---

## Context

A full audit of `mcp/` (the NOVA MCP server) surfaced ~30 findings spanning data integrity, silently-broken features, safety footguns, and integration gaps. To keep PRs reviewable, the remediation was split into three sequential PRs plus a deferred bucket:

- **PR1 — Stability + dormant features** (this branch). Bundles audit categories **A** (data integrity) and **B** (silently-broken features). Invisible to users but foundational; turns dormant features back on.
- **PR2 — Safety / footguns** (audit category **C**). Path-injection and write-allowlist hardening, autocommit safety, MCP-stdio hygiene.
- **PR3 — Integration / completeness** (audit category **D**). Wires `shard_parser` SQLite quick-search as a HUGINN pre-filter; assorted graph/quality fixes.
- **Deferred (E)** — naming polish, summary-index rebuild cost at scale, ravens `top_n` divergence, etc.

Decisions locked during planning:
- `shard_parser` ships **alongside** the float-confidence JSON store (interpretation A — separate `.shard` corpus indexed by SQLite, queried as a HUGINN pre-filter).
- `meta.summary` is **populated at write time** (mirrored from `context.summary` in `update_index` / `patch_index_entry`) rather than changing all readers.
- `pre_compact_fn` ships as a wired **no-op stub** with TODO so future Haiku fact-extraction can drop in without a server change.
- HeavySkill (separate work) lives under `forgemaster/skills/forgemaster-heavyskill.md` with a NOVA shard write per invocation; `source` field uses Option A (`source="agent_inference"`, `theme="heavyskill"`) to avoid touching the closed `ShardCreateInput.source` Literal.

---

## PR1 — Done ✅

47/47 pytest pass. 17 new tests. 12 files modified, 5 new (2 helpers + 3 tests).

| # | Change | Files |
|---|---|---|
| 1 | `mcp/atomic_io.py` — `atomic_write_json` + `atomic_write_text` (tmp + fsync + `os.replace`). Caller still owns `FileLock` | new |
| 2 | Retrofitted **10 JSON writers**: shard, index, summary index, summary markdown, graph, wiki index, wiki schema, nidhogg manifest, evolve config, session flush | store.py, graph.py, wiki.py, nidhogg.py, evolve.py, session_store.py |
| 3 | Added missing `FileLock` to `save_wiki_index` + `save_wiki_schema` | wiki.py |
| 4 | `mcp/timeutils.py` — `parse_iso` (always aware UTC) + `now_utc`. Replaced 7 ad-hoc `datetime.fromisoformat` callsites; naive timestamps now treated as UTC instead of silently swallowing `TypeError` and falsely admitting expired/future shards | timeutils.py + store, maintenance, nott, recall, adversarial, access_log |
| 5 | Per-shard `asyncio.Lock` via `weakref.WeakValueDictionary` in `nova_server.py`. Wraps foreground save + background enrich in both `nova_shard_create` and `nova_shard_update`. Closes the audit's lost-update race when two updates land on the same shard while the first's background enrichment is still running | nova_server.py |
| 6 | `update_index` + `patch_index_entry` mirror `context.summary` → `meta.summary` (without overwriting curated values). Unblocks `adversarial.py` and `recall.py` filters that read `entry["meta"]["summary"]` and were silent no-ops in production | store.py |
| 7 | Quarantine pass now runs on `POST_SPRINT`, `SESSION_START`, `SCHEDULED`, `COUNT_THRESHOLD` (was SCHEDULED-only). Added a fast-path early-exit so `SESSION_START` stays cheap when nothing is quarantined | nott.py |
| 8 | `_pre_compact_stub` wired into `Nott(...)` constructor (TODO comment for future Haiku fact extraction). Hook now actually fires | nova_server.py |
| 9 | `extract_fragments` accepts `max_turns`; caller in `nova_shard_interact` passes `MAX_FRAGMENTS=10` and now gets 10 turns instead of the prior effective 5 (audit found the slice was on rendered half-lines, not turns) | store.py, nova_server.py |
| 10 | Tests: `test_atomic_io.py` (atomicity guarantees, simulated mid-write crash), `test_state_gate_tz.py` (aware vs. naive comparisons, Z-suffix, parse_iso edge cases), `test_meta_summary_populate.py` (mirroring + non-overwrite + full-rescan behavior) | 3 new test files, 17 tests |

**Verification commands:**
```bash
pytest tests/ -q                                                 # 47/47 pass
pytest tests/test_atomic_io.py tests/test_state_gate_tz.py \
       tests/test_meta_summary_populate.py -v                    # 17 PR1-specific tests
```

---

## PR2 — Safety / footguns (next)

Audit category C. Single PR, ~5 items, no schema changes.

| # | Item | Target file(s) | Notes |
|---|---|---|---|
| 1 | `_write_implementation_file` write **allowlist** | `mcp/forgemaster_runtime.py:215+` | Hard-coded set: `output/`, `intake/`. Env-overridable via `FORGEMASTER_WRITE_ROOTS`. Reject `..` traversal + absolute paths outside the allowlist. Audit found this regex-extracts a path out of LLM-generated design docs with no allowlist — an attacker-controlled doc can overwrite skill files |
| 2 | `evolve._auto_commit` positive allowlist + stash-based rollback | `mcp/evolve.py:286-360` | Replace negative deny-list with explicit allow-list of stageable paths. Replace `git checkout -- files` with a named `git stash push -- files` so the user's parallel edits aren't destroyed |
| 3 | `prewarm_embedding_model` → stderr logger | `mcp/nova_embeddings_local.py:41-58` | `print()` on the MCP stdio transport corrupts the JSON-RPC channel. Switch to `logging.warning` (logger already exists in module). Audit other stray `print` callsites along the stdio path while in there |
| 4 | Hook task strong-ref set + `done_callback` | `mcp/hooks.py:69-74` | `asyncio.create_task(handler(**kwargs))` without storing the task: GC may collect it before completion (Python warns), exceptions vanish silently. Add a module-level `set[Task]` keeping strong refs, plus an error-logging done_callback that removes from the set |
| 5 | `Nott._executor` shutdown registration | `mcp/nott.py:166-168` | `ThreadPoolExecutor(max_workers=2)` lives forever; on `evolve.restart_requested` reload, threads pile up. Register `atexit.register(self._executor.shutdown)` or expose a `Nott.close()` and call it from server shutdown |

**Tests to add:**
- `test_forgemaster_write_allowlist.py` — assert `_write_implementation_file` rejects `..` traversal and writes outside `output/` + `intake/`
- `test_evolve_autocommit_safety.py` — patch `git` calls; assert deny-list paths are skipped and rollback uses stash, not `checkout --`

**Risk notes:**
- The allowlist change is potentially behavior-breaking if existing forgemaster sprints depend on writing outside `output/`/`intake/`. Verify by grepping current ticket templates for write targets before merging.
- The autocommit change should keep the "auto-commit" feature working for the happy path; only the safety guard rails change.

---

## PR3 — Integration / completeness

Audit category D. May split into 2 PRs if shard_parser wiring grows.

### D1 — `shard_parser` SQLite pre-filter wiring (largest item)

Per the locked decision (interpretation A), `.shard` files are a separate **curated facts corpus** with discrete `{-1, 0, 1}` confidence, indexed by `ShardDB` (SQLite). They live alongside the JSON conversation shards, which keep float confidence and the existing pipeline.

**Architecture:**

```
nova_shard_interact(message)
   ├── nova_facts_search(message)   ← NEW pre-filter
   │     SQLite FTS over ShardDB; returns high-confidence (+1) facts
   ├── HUGINN.retrieve(message)     ← existing path
   │     dense embedding over JSON shards
   └── merge: facts (always included) ∪ HUGINN top-K
```

**Implementation tasks:**

1. Decide `.shard` file root — propose `facts/` at repo root, env `NOVA_FACTS_DIR`. Add to `.gitignore` if user-personal, or keep tracked if curated team-shared (ask).
2. New `nova_facts_search` MCP tool in `nova_server.py` — wraps `ShardDB.search()` (already in `shard_parser.py`).
3. SQLite index lives at `facts_index.db`, rebuilt by NÓTT on `SESSION_START` (cheap because it's a separate small corpus).
4. Modify `nova_shard_interact` (`nova_server.py:282`-ish) to call `nova_facts_search` first, prepend results to the loaded set, then run HUGINN.
5. Tests: `test_facts_search.py` (FTS happy path, discrete-confidence filter, +1 only by default), `test_shard_interact_facts_pre_filter.py` (integration: facts surface alongside HUGINN hits).

**Open question to resolve before starting D1:** is `facts/` curated team-shared (commit it) or user-personal (gitignore it)? Recommend user-personal initially with an `examples/` subfolder that is tracked.

### D2 — Smaller integration fixes

| Item | File | Notes |
|---|---|---|
| Startup `CLAUDE_API_KEY` absence warning | `mcp/nova_server.py` (init) | `logging.warning` listing degraded features (HUGINN, MUNINN, summaries) when key missing. Today: silent local-fallback |
| `nidhogg.add_corroborated_by` writes shard ids, not paths | `mcp/nidhogg.py:424` | Currently passes a filesystem path as a graph target id, polluting the graph. Need an "ingested-doc → shard id" mapping during `nidhogg_ingest` |
| `query_graph_transitive` BFS → `deque.popleft` | `mcp/graph.py:131` | Currently `list.pop(0)` = O(n²) |
| `add_relation` symmetric-edge canonicalization | `mcp/graph.py:69-74` | Sort endpoints for symmetric edge types (`contradicts`, `merges_with`, `co_occurs`) so A↔B isn't stored as two edges |

---

## Deferred (E) — won't land until prompted

- `nova_shard_get` vs. `nova_shard_get_full` naming (semantics reversed from user expectation)
- `wiki._parse_frontmatter` hand-rolled YAML subset → `pyyaml` (breaks on commas-in-tags)
- Summary-index O(N) rebuild on every shard write (becomes a real cost only at >1k shards)
- `evolve._FOCUS_AREAS` rotation policy (4 of 6 areas starved)
- `nova_shard_list` deprecation cleanup (still loads full conversation_history for every shard)
- `Huginn.retrieve` `top_n` divergence between `nova_shard_interact` (default 5) and `nova_shard_search` (caller-passed)
- `shard_parser` discrete vs. float confidence — both stay; documented as intentional in this file

---

## How to resume

If picking this up in a fresh session:

```bash
git checkout pr1-stability-and-dormant-features
git log --oneline -3                           # confirm baseline + PR1 commits
pytest tests/ -q                               # confirm clean
```

Then either:
- **Open PR1** for review and start PR2 on a new branch off `pr1-stability-and-dormant-features` (recommended — PR2 doesn't depend on PR1 internals but stacks cleanly).
- **Continue straight into PR2** on the current branch if not gating on review.

`forgemaster/skills/forgemaster-heavyskill.md` (added pre-PR1) is independent of this remediation track and is already live in the codebase.
