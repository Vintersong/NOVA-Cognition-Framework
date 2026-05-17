# Brief: Document every Python script in `utilities/`

You are Sonnet 4.6. Your job is to produce one short design document per Python script in `utilities/`, saved to `docs/design/utilities/<script_stem>.md`. This is **documentation work, not refactoring** — do not modify any `.py` file.

## Why this exists

NOVA was built largely in the user's head, in conversations with Claude, and in chats with Perplexity. The rationale behind most scripts is not captured anywhere on disk. A rewrite of the core shard model is already underway (`mcp/shard_parser.py` introduces a new `.shard` format and discrete confidence `{-1, 0, 1}`), which means the existing utilities will soon need to be audited for compatibility. Before that audit is possible, someone needs to know what each script was supposed to do and why it existed.

This brief covers `utilities/` only. `mcp/` will follow in later sessions.

## Scope

Document exactly these files (10 total):

- `utilities/autoresearch.py`
- `utilities/chatgpt_to_nova.py`
- `utilities/check_tool_docs.py`
- `utilities/dedup_json.py`
- `utilities/shard_compact.py`
- `utilities/shard_index.py`
- `utilities/test_shards.py`
- `utilities/theme_analyzer.py`
- `utilities/usage_rollup.py`
- Any `.py` file inside `utilities/autoresearch_loop/` (check the directory, document each file found there the same way)

Skip `__pycache__/`.

## Output format

One file per script at `docs/design/utilities/<script_stem>.md`. Create the directory if it doesn't exist. Each file must follow this skeleton exactly — keep it to **one page**:

```markdown
# <script_stem>.py

**One-line purpose:** <what this script does, in a single sentence>

## Why it exists
<The problem this script solves. What wasn't possible without it. If the rationale is not derivable from code or comments, write: `_Rationale not recoverable from source — user narration needed._`>

## Key concepts
<Any domain vocabulary a reader needs. E.g. "shard", "meta_tags", "confidence decay". One bullet per term, definition in plain English. Skip this section if there is no non-obvious vocabulary.>

## Public surface
<Functions, classes, or CLI entry points that other code or the user invokes. For each, give signature and one line on what it does. If the script is CLI-only, document the argparse/sys.argv contract here.>

## Inputs and outputs
<Files read, files written, env vars consumed, stdout/stderr behavior. Be concrete — exact paths where possible.>

## Invariants and assumptions
<Things that must stay true for this script to work: file formats it expects, directory layouts, shard schema fields, external services. One bullet per invariant.>

## Callers and integration
<Who runs this script: cron, manual CLI, imported by another module, tests. If nothing calls it and it looks orphaned, say so explicitly.>

## Known gaps / open questions
<Things you could not determine from the code. Bugs you spotted but did not fix. Assumptions the script makes that may no longer hold (e.g. float confidence vs. the new discrete model in `mcp/shard_parser.py`). List as questions for the user.>
```

## Hard rules

1. **Do not hallucinate rationale.** If a script's "why" is not evident from the code, comments, commit history, or `CLAUDE.md`, write the flagged placeholder exactly: `_Rationale not recoverable from source — user narration needed._` The user will fill these in later. A wrong guess is worse than a flagged gap.
2. **Do not modify any Python file.** Read-only on `.py`. You may create markdown and the `docs/design/utilities/` directory.
3. **One page per doc.** If a script is trivial, the doc is short. Do not pad.
4. **No multi-paragraph prose where a bullet list works.** Terse is correct.
5. **Flag cross-module drift.** If a utility assumes the old JSON shard format with float confidence, note it under "Known gaps" — the user is aware `mcp/shard_parser.py` introduces a new model and needs to know which utilities will break when the migration happens.
6. **No emojis.**

## Process per script

1. Read the full script.
2. Grep the repo for imports and CLI references: `grep -r "<script_stem>" --include="*.py" --include="*.md"`. Note who calls it.
3. Check `CLAUDE.md` and `README.md` for any mention of the script — that's often the only rationale on disk.
4. Inspect what it imports from `mcp/` (if anything) to understand what data model it assumes.
5. Draft the markdown. Be factual. Where you don't know, flag — don't invent.
6. Save to `docs/design/utilities/<script_stem>.md`.

## When you're done

Write a one-paragraph summary to stdout listing: which scripts were documented, how many "user narration needed" flags you left total, and any surprising findings (orphaned scripts, obvious bugs, scripts that clearly assume the old shard model). Do not write this summary as a file — it goes to the user directly for review.

## What not to do

- Don't write an "overview" or "index" doc for `utilities/` as a whole. One doc per script, nothing else.
- Don't update `CLAUDE.md`, `README.md`, or any existing file.
- Don't propose refactors. This is an archaeology pass, not a redesign pass.
- Don't run the scripts to see what they do — read the source. Running them may touch shards or external APIs.
