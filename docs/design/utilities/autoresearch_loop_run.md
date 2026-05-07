# autoresearch_loop/run.py

**One-line purpose:** Run a self-directed, scored research loop where a local LM Studio model proposes its own queries, executes them, self-evaluates each answer, and writes high-scoring results as NOVA shards.

## Why it exists

_Rationale not recoverable from source — user narration needed._

The pattern (sometimes called "Karpathy-style autoresearch") automates knowledge generation without a human in the loop: the model reads a `program.md` research agenda, decides what to investigate next, answers it, scores the answer, and keeps only results above a threshold. The intent appears to be growing the NOVA shard base autonomously during idle time, guided by a maintained research agenda file.

## Key concepts

- **`program.md`** — A markdown file in `utilities/autoresearch_loop/` that defines active research directions, kept shards, and discarded attempts. The loop reads it each iteration and appends to it after each result.
- **KEEP threshold** — Integer 1–10; answers scoring >= `KEEP_THRESHOLD` (default 7, env `AUTORESEARCH_THRESHOLD`) are written as shards. Lower scores are discarded.
- **self-evaluation** — The same local model that produced the answer also rates it against a fixed rubric. This is cheap but biased (models tend to rate their own output highly).
- **`results.tsv`** — Append-only log of every iteration: timestamp, score, decision, query, shard_id.

## Public surface

- `run_loop(max_iterations, dry_run)` — Main loop; runs indefinitely unless interrupted or `max_iterations` reached.
- `write_shard(query, content, score, model) → shard_id` — Write a kept result as a NOVA shard under `shards/autoresearch/`.
- `print_status()` — Print a summary of `results.tsv` (kept/discarded/errors, average score).
- `append_result(row)` / `load_results()` — TSV log I/O.
- `update_program_md(query, score, decision, summary)` — Append to `program.md` kept/discarded sections.
- CLI: `python run.py [--max N] [--dry-run] [--status]`

## Inputs and outputs

- **Reads:** `autoresearch_loop/program.md` (each iteration), `autoresearch_loop/results.tsv`.
- **Writes:** `shards/autoresearch/<shard_id>.json` for kept results; `autoresearch_loop/results.tsv` (appended); `autoresearch_loop/program.md` (updated with findings).
- **Env:** `NOVA_SHARD_DIR` (default: `../../shards`), `LM_STUDIO_URL` (default `http://127.0.0.1:1234`), `AUTORESEARCH_THRESHOLD` (default 7), `AUTORESEARCH_DELAY` (default 2), `LM_MODEL` (fallback model name).
- **Requires:** `anthropic` package, LM Studio running locally.

## Invariants and assumptions

- `program.md` must exist in `autoresearch_loop/` before the loop starts — the script does not create it.
- The local model must support the Anthropic messages API (LM Studio exposes an Anthropic-compatible endpoint).
- Confidence in written shards is derived from score: `min(0.5 + score * 0.05, 0.95)` — still a float, assumes old shard model.
- Self-evaluation uses the same model that produced the answer; scores may be inflated.

## Callers and integration

Manual CLI only. Not imported by any other module. Separate from `utilities/autoresearch.py` (which uses a fixed topic list and Gemini). The two scripts are independent parallel approaches to autoresearch. Referenced in `forgemaster/agents/research/README.md` and `forgemaster/agents/research/program.md`.

## Known gaps / open questions

- Self-scoring is known to be unreliable — a model scoring its own output will bias toward high scores. Has this been evaluated in practice?
- The local model (`LM Studio`) has no web search capability, unlike `autoresearch.py`'s Gemini path. Research quality is limited to training cutoff.
- Confidence stored as float will conflict with `mcp/shard_parser.py`'s new discrete model.
- Shards are written to `shards/autoresearch/` subdirectory — `shard_index.py` scans only flat `shards/*.json`, so these shards will not appear in the index unless the index builder is updated to recurse.
- `program.md` is updated by string replacement of sentinel comments (`*(populated automatically ...)*`). If those sentinels are edited or removed, `update_program_md` silently does nothing.
