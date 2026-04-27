# dedup_json.py

**One-line purpose:** Detect and delete exact-duplicate NOVA shards by hashing their message content.

## Why it exists

The ChatGPT migration (`chatgpt_to_nova.py`) and manual shard creation can produce shards with identical conversation content under different filenames. Duplicate shards inflate search results and waste context window space. This script provides a safe way to find and remove them.

## Key concepts

- **content hash** — An MD5 digest of the shard's message pairs (user+ai or author+content), JSON-serialised in sorted-key order. Two shards with identical message content will produce the same hash regardless of filename, timestamps, or metadata.
- **first-seen wins** — When duplicates are found, the first file encountered in sorted directory order is kept; all subsequent files with the same hash are deleted.

## Public surface

- `normalize_content(data) → str | None` — Extracts message pairs and returns their MD5 hash. Returns `None` if neither `messages` nor `conversation_history` key is present.
- `find_duplicates() → list[tuple[str, str]]` — Scans `SHARD_DIR`, returns `(duplicate_path, original_filename)` pairs.
- `main()` — CLI entry point; accepts `--dry-run` via `sys.argv`.
- CLI: `python dedup_json.py [--dry-run]`

## Inputs and outputs

- **Reads:** all `*.json` files in `SHARD_DIR` (from `shard_index.py`).
- **Deletes:** duplicate shard files (unless `--dry-run`).
- **Calls:** `update_index()` from `shard_index.py` after deletions to keep the index consistent.
- **Env:** `NOVA_SHARD_DIR` (via `shard_index.py` import).

## Invariants and assumptions

- Imports `SHARD_DIR` and `update_index` directly from `utilities/shard_index.py` — must be run from `utilities/` or have `utilities/` on `sys.path`.
- Supports two shard turn formats: `{user, ai}` (NOVA native) and `{author, content}` (legacy / ChatGPT-migrated).
- Shards without a `messages` or `conversation_history` key are skipped silently (hash returns `None`).
- Deletion is permanent — no backup is made before removing duplicates.

## Callers and integration

Manual CLI only. Not imported by any other module. Mentioned in `README.md`, `CLAUDE.md`, and `docs/ROADMAP.md` as a maintenance utility.

## Known gaps / open questions

- Hash is MD5 over message content only — metadata differences (different `guiding_question`, `theme`, `confidence`) are ignored. A duplicate is defined purely by conversation content. Is that correct, or should guiding question also be part of the hash?
- No backup before deletion. Consider adding a `--backup-dir` option.
- `shard_index.py` import means the script cannot be run from outside `utilities/` without path manipulation.
- Does not handle the new `.shard` file format from `mcp/shard_parser.py`.
