# Brief: Document every Python script in `mcp/`

You are Sonnet 4.6. Your job is to produce one short design document per Python script in `mcp/`, saved to `docs/design/mcp/<script_stem>.md`. This is **documentation work, not refactoring** — do not modify any `.py` file.

## Why this exists

The utilities documentation pass (`docs/design/utilities/`) revealed five shard-writing scripts all using float confidence, a wrong default shard directory in two scripts, and a stub file. The `mcp/` directory is the core server — it has the same archaeology problem but higher stakes: bugs here affect every tool call. A refactor of the shard model is already underway (`mcp/shard_parser.py` introduces a new `.shard` format with discrete confidence `{-1, 0, 1}` replacing the old float). Before that migration can proceed safely, each module needs to be documented for what it assumes, what it produces, and where it will break.

## Scope

Document exactly these files (25 total):

**Core server**
- `mcp/nova_server.py`
- `mcp/config.py`
- `mcp/schemas.py`
- `mcp/models.py`

**Shard I/O and index**
- `mcp/store.py`
- `mcp/shard_parser.py`

**Knowledge graph**
- `mcp/graph.py`

**Maintenance**
- `mcp/maintenance.py`
- `mcp/nott.py`

**Search and retrieval**
- `mcp/ravens.py`
- `mcp/nova_embeddings_local.py`
- `mcp/build_summary_index.py`

**Session and runtime**
- `mcp/session_store.py`
- `mcp/forgemaster_runtime.py`

**Tool modules**
- `mcp/evolve.py`
- `mcp/nidhogg.py`
- `mcp/wiki.py`
- `mcp/wiki_ingest.py`
- `mcp/wiki_tools.py`

**Infrastructure**
- `mcp/permissions.py`
- `mcp/hooks.py`
- `mcp/usage.py`

**Gemini worker**
- `mcp/Gemini/gemini_mcp.py`
- `mcp/Gemini/test_gemini.py`

**Tests**
- `mcp/test_nova.py`

Skip `__pycache__/`.

## Output format

One file per script at `docs/design/mcp/<script_stem>.md`. For `mcp/Gemini/` files use `docs/design/mcp/gemini_<script_stem>.md`. Create the directory if it doesn't exist. Each file must follow this skeleton exactly — keep it to **one page**:

```markdown
# <script_stem>.py

**One-line purpose:** <what this script does, in a single sentence>

## Why it exists
<The problem this script solves. What wasn't possible without it. If the rationale is not derivable from code or comments, write: `_Rationale not recoverable from source — user narration needed._`>

## Key concepts
<Any domain vocabulary a reader needs. One bullet per term, definition in plain English. Skip this section if there is no non-obvious vocabulary.>

## Public surface
<Functions, classes, or CLI entry points that other code or the user invokes. For each, give signature and one line on what it does. If the script is CLI-only, document the argparse/sys.argv contract here.>

## Inputs and outputs
<Files read, files written, env vars consumed, stdout/stderr behavior. Be concrete — exact paths where possible.>

## Invariants and assumptions
<Things that must stay true for this script to work: file formats it expects, directory layouts, shard schema fields, external services. One bullet per invariant.>

## Callers and integration
<Who calls this module: imported by which other files, registered into the MCP server, run as CLI. If nothing calls it and it looks orphaned, say so explicitly.>

## Known gaps / open questions
<Things you could not determine from the code. Bugs you spotted but did not fix. Assumptions the script makes that may no longer hold — especially float confidence vs. the new discrete model in `mcp/shard_parser.py`. List as questions for the user.>
```

## Hard rules

1. **Do not hallucinate rationale.** If a module's "why" is not evident from the code, comments, commit history, or `CLAUDE.md`, write the flagged placeholder exactly: `_Rationale not recoverable from source — user narration needed._`
2. **Do not modify any Python file.** Read-only on `.py`. You may create markdown and the `docs/design/mcp/` directory.
3. **One page per doc.** If a module is small, the doc is short. Do not pad.
4. **No multi-paragraph prose where a bullet list works.** Terse is correct.
5. **Flag schema assumptions explicitly.** Every module that reads or writes shard JSON must have a "Known gaps" note stating whether it uses float confidence or is already on the discrete `{-1, 0, 1}` model, and what will break when the migration completes.
6. **Flag the shard_parser boundary.** `mcp/shard_parser.py` is the new model. Every module that bypasses it and reads raw JSON directly is a migration risk — call that out.
7. **No emojis.**

## Process per script

1. Read the full script.
2. Grep the repo for imports of this module: `grep -r "<script_stem>" --include="*.py" --include="*.md"`. Note who calls it.
3. Check `CLAUDE.md` for any description of this module's role.
4. Note which env vars from `mcp/config.py` the module consumes.
5. Draft the markdown. Where you don't know, flag — don't invent.
6. Save to `docs/design/mcp/<script_stem>.md`.

## Priority order

Document in this order so that foundational modules are understood before the ones that depend on them:

1. `config.py`
2. `schemas.py`
3. `models.py`
4. `shard_parser.py`
5. `store.py`
6. `graph.py`
7. `maintenance.py`
8. `nova_embeddings_local.py`
9. `ravens.py`
10. `build_summary_index.py`
11. `session_store.py`
12. `permissions.py`
13. `hooks.py`
14. `usage.py`
15. `nova_server.py`
16. `nott.py`
17. `forgemaster_runtime.py`
18. `evolve.py`
19. `nidhogg.py`
20. `wiki.py`
21. `wiki_ingest.py`
22. `wiki_tools.py`
23. `Gemini/gemini_mcp.py`
24. `Gemini/test_gemini.py`
25. `test_nova.py`

## When you're done

Write a one-paragraph summary to stdout listing: which scripts were documented, how many "user narration needed" flags you left total, and any surprising findings — especially modules that read raw shard JSON directly (migration risk), circular dependencies, or dead code. Do not write this summary as a file — it goes to the user directly.

## What not to do

- Don't write an "overview" or "index" doc for `mcp/` as a whole. One doc per script, nothing else.
- Don't update `CLAUDE.md`, `README.md`, or any existing file.
- Don't propose refactors. This is an archaeology pass.
- Don't run the scripts.
