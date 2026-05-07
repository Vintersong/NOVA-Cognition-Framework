# chatgpt_to_nova.py

**One-line purpose:** Convert a ChatGPT data export (JSON files) into NOVA shards, one shard per conversation.

## Why it exists

When the project switched to NOVA as its primary memory system, prior conversations from ChatGPT represented a significant body of knowledge that would have been lost. This script is a one-shot migration tool: point it at a ChatGPT export directory, and it produces ready-to-index NOVA shards.

## Key concepts

- **ChatGPT export format** — OpenAI's data export produces `conversations-000.json` (and numbered siblings), each containing a list of conversation objects. Each conversation stores messages as a tree (supporting edits/branches) under a `mapping` dict, with a `current_node` pointer to the active branch tip.
- **Linear history** — The script walks backwards from `current_node` to root to reconstruct the main-branch message sequence, discarding alternate branches.
- **Theme / intent inference** — Keyword matching on title + first 3 user turns. Heuristic only; no embeddings.

## Public surface

- `migrate(input_dir, output_dir, min_turns, dry_run)` — Top-level function; loads, converts, and writes all conversations.
- `conversation_to_shard(conv, existing_ids) → dict | None` — Converts a single conversation dict to a shard dict.
- `build_linear_history(conv) → list[dict]` — Extracts ordered user/assistant turns from the tree structure.
- CLI: `python chatgpt_to_nova.py [--input DIR] [--output DIR] [--min-turns N] [--dry-run]`

## Inputs and outputs

- **Reads:** `conversations-*.json` (or any `*.json`) from `--input` directory (default `./chatgpt_export`).
- **Writes:** `<shard_id>.json` per conversation to `--output` directory (default: `NOVA_SHARD_DIR` env var or `shards/`).
- **Env:** `NOVA_SHARD_DIR`.
- **Stdout:** progress report and per-theme breakdown.

## Invariants and assumptions

- Input JSON files must be arrays of conversation objects (standard ChatGPT export format), or a single conversation dict.
- The `mapping` field must contain a `current_node` pointer; conversations without it produce empty history and are skipped.
- Output dir is created if absent.
- `"confidence": 1.0` (float) is hardcoded for all imported shards — assumes old float shard model.

## Callers and integration

Manual CLI, one-time migration use. Not imported by any other module. Mentioned in `README.md`, `CLAUDE.md`, and `docs/ROADMAP.md` as a migration tool.

## Known gaps / open questions

- Confidence is hardcoded to `1.0` for all imported shards regardless of content quality. Is that intentional?
- Theme and intent inference are keyword heuristics over the first 3 turns — accuracy on older, varied conversations is unknown.
- Does not call `nova_shard_index` or `nova_shard_consolidate` after writing; the user must run those manually (the script hints at this in its final stdout line).
- Writes `"conversation_history"` with `{user, ai, timestamp}` turn format — check compatibility with current `mcp/store.py` reader.
- Float confidence will conflict with the new discrete `{-1, 0, 1}` model in `mcp/shard_parser.py`.
