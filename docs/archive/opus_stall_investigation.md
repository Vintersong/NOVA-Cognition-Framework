# Investigation Brief: nova_shard_search stall (6+ minutes)

## What happened

A call to the `nova_shard_search` MCP tool hung for over 6 minutes before being user-interrupted. No error was returned. The call was:

```python
nova_shard_search(query="shard_parser migration epistemic upgrade", top_n=5)
```

This was the first `nova_shard_search` call of the session (the earlier `nova_shard_interact` call succeeded). The session had been active for roughly 30–40 minutes before this call was attempted.

## Your job

Find the root cause. Do not fix anything — investigate and report findings. Read source files only. Do not run the server or any scripts.

## Where to look

All source files are under `mcp/` in the project root.

### 1. The call path for `nova_shard_search`

Trace what `nova_shard_search` does in `mcp/nova_server.py`. Identify every blocking step: file I/O, embedding model calls, API calls, lock acquisitions. Note anything that could block indefinitely.

### 2. HUGINN retrieval (`mcp/ravens.py`)

`nova_shard_search` calls `Huginn.retrieve()`. Read `ravens.py` carefully:
- Does `_local_retrieve` do anything that could block? (file reads, embedding calls)
- If `CLAUDE_API_KEY` is set, HUGINN makes a Haiku API call. What timeout is applied? Check `_RAVEN_API_TIMEOUT` and how `asyncio.wait_for` is used. Is there any code path where the timeout is not applied?
- Is there a case where the LLM call is made but the result is awaited without a timeout guard?

### 3. Embedding model (`mcp/nova_embeddings_local.py`)

`nova_shard_search` may call `generate_local_embedding` for the query. Read `nova_embeddings_local.py`:
- Is the embedding model loaded lazily on first call? If so, could a slow first-load block the event loop?
- Is `prewarm_embedding_model()` called at startup? If the prewarm thread is still running when the first embedding call hits, is there a lock contention scenario?
- Is any lock (`threading.Lock`) held while inference runs? Could a second caller block on that lock?

### 4. MUNINN (`mcp/ravens.py`)

If HUGINN's confidence is below `HUGINN_CONFIDENCE_THRESHOLD`, MUNINN runs. Read the MUNINN path:
- Does MUNINN also make an API call? Same timeout question as HUGINN.
- Does MUNINN read shard files directly for the turns preview? If so, is there a case where it tries to read a shard file that is locked by NÓTT's thread pool?

### 5. NÓTT contention (`mcp/nott.py`)

The earlier `nova_shard_interact` call fired a `SESSION_START` hook → NÓTT decay pass. That NÓTT pass runs in a `ThreadPoolExecutor`. By the time `nova_shard_search` was called (~30–40 minutes later), the NÓTT thread should have finished. But check:
- Is there any global lock or file lock that NÓTT holds and that `nova_shard_search` also needs?
- Does `_decay_pass_sync` or `_compact_pass_sync` hold any lock that would block `nova_shard_search` file reads?

### 6. `shard_index.json` read

`nova_shard_search` passes the index to HUGINN. How is `shard_index.json` loaded in `nova_server.py`? Is there a lock on it? If NÓTT is writing to it concurrently, could the reader block?

### 7. asyncio / thread pool interaction

The NOVA server is asyncio-based. Check `nova_server.py`:
- Are any blocking calls made directly in the async handler (without `asyncio.to_thread` or `run_in_executor`)?
- If `generate_local_embedding` is called synchronously in an async context, it would block the entire event loop for the duration of inference.

## What to report

For each of the 7 areas above:
- What you found (code path, line numbers)
- Whether it is a plausible stall cause
- The specific condition under which it would stall (e.g. "blocks if NÓTT is still running", "blocks if embedding model not yet loaded")

Rank the candidates by likelihood. If you find a definitive cause, state it clearly. If you find multiple plausible causes, list them in order.

Do not propose fixes. Report findings only.
