# NOVA Changes — Implementation List

## Priority order (do in sequence)

### 1. Provenance fields on every shard — DONE 2026-04-27
**Why:** Without it you can't debug six months of belief drift. Highest leverage, lowest cost.

**How:**
- Add `source` field to shard schema with structured values: `user_input`, `external_doc`, `agent_inference`, `session_extracted`, `corroborated_by`
- Schema migration: backfill existing 446 shards with best-guess source based on creation context (default `agent_inference` for ambiguous cases)
- Update enrich_shard to set source automatically based on which ingestion path wrote the shard
- Update retrieval to weight `agent_inference` lower than externally-sourced shards by default

---

### 2. Confidence floor + cluster-aware deduplication for hook recall
**Why:** Wrong injection is worse than no injection. Inject only what clears a high precision bar.

**How:**
- Hook recall: hard floor at 0.85 confidence, max 3 shards, no top-K floor (return zero if nothing clears)
- If multiple top hits belong to the same cluster, collapse to one representative + pointer to siblings
- Inject summary headers (~50 tokens), not full bodies; full body only via explicit MCP call

---

### 3. Headers-only injection by default — DONE 2026-04-27 (summary field deferred)
**Why:** Token math compounds. Worker context stays clean. Critic Pass gets the right shape.

**How:**
- Every shard needs a `summary` field (~50 tokens) and a `body` field (full content)
- Hook injects summary only
- Add explicit MCP tool `nova_shard_get_full` for cold-path full-body fetch
- Workers consume structure, not text

---

### 4. Markdown as source of truth (Dory pattern)
**Why:** Human-readable substrate. Obsidian compatibility. Visibility for debugging drift.

**How:**
- Shards stay SQL-primary for retrieval performance
- Add markdown export layer that mirrors shards as `.md` files in a corpus directory
- Bidirectional sync: edits to markdown flow back into SQL on next NÓTT pass
- Wiki layer renders to markdown directly
- Browser wiki endpoint (read-only first, then auth-gated edit) for inspection

---

### 5. Two-hook prototype for Claude Code passive integration
**Why:** Test the push-based recall hypothesis on real sessions. Generates outcome data.

**How:**
- Hook 1 — `UserPromptSubmit`: runs `nova-recall --top-k 3 --min-confidence 0.85` and prepends results to prompt
- Hook 2 — `PostToolUse` on Edit/Write: runs `nova-extract --from-tool-result` to capture decisions as new shards
- Cache layer: in-memory LRU keyed on `(query_hash, top_k, min_confidence)`, 5-minute TTL
- Tool whitelist: hooks fire only on Read/Grep/Edit/Write, skip Bash/ls/pwd
- Run on own Forgemaster sessions for one week, log every injection and whether it helped

---

### 6. Quarantine for session-extracted shards
**Why:** Breaks the feedback loop. Session-extracted shards can't dominate retrieval until corroborated.

**How:**
- New field `quarantine_until` (timestamp) on shards written by `PostToolUse`
- Quarantine window: N hours or N sessions (start with 48 hours)
- Quarantined shards retrievable but with confidence penalty
- Graduate after window if nothing contradicts; mark as contradicted (-1) if something does

---

### 7. Lineage edges (`supersedes`, `corroborated_by`)
**Why:** Richer than `contradicts`. Makes graph queryable for debugging.

**How:**
- Add edge types: `supersedes` (with reason field), `corroborated_by`
- When a new shard contradicts an existing one and wins, write `supersedes` with explanation
- When a new external source confirms an existing belief, write `corroborated_by`
- Confidence escalation only allowed via `corroborated_by` edges, not via repeat retrieval

---

### 8. Decay-on-read
**Why:** Frequent retrieval without corroboration suggests stale anchor. Force shards to earn position.

**How:**
- Every retrieval logs the shard ID
- NÓTT pass: shards retrieved N times in window M without new corroboration get small confidence penalty
- Inverts the natural reinforcement bias

---

### 9. Outcome-based reinforcement loop
**Why:** Same problem as the GP surrogate's exogenous benchmark. Connect Forgemaster sprint success/failure to contributing shards.

**How:**
- Forgemaster runtime already tracks which shards informed which sprints
- After sprint completion, write outcome edge from sprint shard to contributing shards
- Successful sprints reinforce contributing shards (raise confidence via corroborated_by)
- Failed sprints flag contributing shards for review (write contradicts edge if root-caused to bad shard)

---

### 10. State-aware retrieval gating (AutoAdapt pattern)
**Why:** Relevance alone isn't enough. Filter by applicability to current state.

**How:**
- Add precondition fields to shards (project context, validity window, superseded_by reference)
- Retrieval pipeline: rank by relevance, then filter by state applicability before injection
- A high-relevance shard about old NOVA architecture gets filtered when current project state is new architecture

---

### 11. Cluster definition (Leiden community detection)
**Why:** The simplex constraint is meaningless without stable cluster boundaries.

**How:**
- NÓTT runs Leiden community detection on the knowledge graph during scheduled passes
- Cluster membership cached as a column on each shard
- Simplex sum=1 invariant enforced per-cluster via SQL trigger
- Recompute clusters when graph topology changes meaningfully (NÓTT decides threshold)

---

### 12. Adversarial NÓTT pass
**Why:** Outside grader checks confident beliefs against external standard. Circuit breaker for echo chamber.

**How:**
- Weekly cycle: take top N highest-confidence shards
- Run them through a non-Claude model (Gemini or local Nemotron) for contradiction hunting
- Findings become `contradicts` edges with provenance back to the adversarial pass
- Different training distribution = independent error pattern

---

## Things explicitly deferred

- **GP surrogate (decay tuner)** — wait until exogenous benchmark exists. Until then it has nothing honest to learn from.
- **Critic Pass writing verdicts to NOVA** — wait until quarantine + lineage edges + hop-limited propagation are all in place. Verdict blast radius is unbounded otherwise.
- **Visibility/inspection tools** — build incrementally as you need them, not upfront.
- **Anything from Dee's Dory beyond markdown sync** — interesting but not differentiating for NOVA's epistemic angle.

---

## Sequence rationale

1–3 are infrastructure for everything else.
4 is parallel work that unlocks debugging.
5 is the experiment that generates outcome data.
6–8 break feedback loops once you have data.
9 closes the outcome loop.
10–11 unblock the simplex math.
12 is the long-term circuit breaker.

Don't reorder. Each step depends on the previous being correct.