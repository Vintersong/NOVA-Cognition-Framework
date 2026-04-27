# autoresearch.py

**One-line purpose:** Run a fixed list of research topics through Gemini Flash (with Google Search grounding) or a local LM Studio model and save each result as a NOVA shard.

## Why it exists

The NOVA knowledge base needed a way to pull in structured research on recurring topics (QA patterns, LLM routing, skill file design, SlopCodeBench) without manual copy-paste. This script automates that: one run populates several shards, tagged and ready for search.

## Key concepts

- **shard** — A JSON file in `shards/` representing one unit of knowledge with a guiding question, conversation turns, theme, intent, and confidence score.
- **autoresearch** — A shard tagged `autoresearch` was machine-generated, not from a human conversation.
- **Google Search grounding** — Gemini's `GoogleSearch` tool config causes the model to retrieve live web results before answering, reducing hallucination risk.

## Public surface

- `run(topics, dry_run, backend)` — Main loop; iterates over topic dicts, queries the backend, writes shards.
- `query(prompt, backend, local_model) → (content, backend_used)` — Unified query dispatch; tries Gemini, falls back to LM Studio if `backend="auto"`.
- `write_shard_direct(topic, content, backend) → shard_id` — Writes one shard JSON directly to disk (bypasses MCP server).
- CLI: `python autoresearch.py [--topic TAG] [--dry-run] [--backend auto|gemini|local]`

## Inputs and outputs

- **Reads:** nothing on disk; topics are hardcoded in `RESEARCH_TOPICS`.
- **Writes:** `shards/autoresearch-<8hex>.json` per topic.
- **Appends:** `nova_autoresearch.jsonl` at repo root — one JSON line per run with all results.
- **Env:** `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.5-flash`), `QWEN_BASE_URL` (default `http://127.0.0.1:1234`), `QWEN_MODEL`, `NOVA_SHARD_DIR`.

## Invariants and assumptions

- `NOVA_SHARD_DIR` must be writable; the script creates it if absent.
- Gemini 503 retries are capped at 3 with a 30-second delay; other errors abort immediately.
- LM Studio must be running and serving a model on `QWEN_BASE_URL` for the local fallback to work.
- Shards are written with `"confidence": 0.7` (float) — assumes the old shard model.

## Callers and integration

Manual CLI only. Not imported by any other module. `autoresearch_loop/run.py` is a separate, more advanced loop (does not call this file).

## Known gaps / open questions

- All shards are written with `"confidence": 0.7` (float). The new `mcp/shard_parser.py` model uses discrete `{-1, 0, 1}` — shards written here will not be valid under the new schema.
- `RESEARCH_TOPICS` is hardcoded in the file; there is no external topic file. Adding new topics requires editing the script.
- The shard schema differs slightly from what `nova_shard_create` (MCP tool) produces — no `conversation_history` key, uses `turns` instead. Is this intentional or a drift?
