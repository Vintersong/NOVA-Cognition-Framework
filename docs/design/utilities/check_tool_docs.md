# check_tool_docs.py

**One-line purpose:** Verify that `CLAUDE.md`, `mcp/SKILL.md`, and `mcp/schemas.py` all mention the correct total tool count and every tool name registered in `mcp/nova_server.py`.

## Why it exists

_Rationale not recoverable from source — user narration needed._

The script exists as a consistency guard: when a new tool is added to `nova_server.py`, it is easy to forget to update the count phrase in `CLAUDE.md` or `SKILL.md`. This script catches that drift automatically.

## Key concepts

- **`_ALL_TOOL_NAMES`** — A list literal assigned at module level in `mcp/nova_server.py`. This is the source of truth for which tools are registered. The script extracts it via `ast.parse` (static analysis, no import).
- **count phrase** — Any sentence in the checked docs that mentions both the word "tool(s)" and the exact integer count, within 40 characters of each other. Regex match, case-insensitive.

## Public surface

- `main() → int` — Runs all checks, prints findings, returns 0 (pass) or 1 (fail). Entry point via `if __name__ == "__main__"`.
- No argparse — no CLI flags; pass/fail is signalled through exit code.

## Inputs and outputs

- **Reads:** `mcp/nova_server.py`, `mcp/schemas.py`, `mcp/SKILL.md`, `CLAUDE.md` — all via absolute paths relative to the repo root.
- **Writes:** nothing.
- **Stdout:** one line per error, or a single pass line.
- **Exit code:** 1 on any failure, 0 on success.

## Invariants and assumptions

- `_ALL_TOOL_NAMES` must be a top-level list or tuple literal in `nova_server.py`; nested, computed, or imported names are not detected.
- The "count phrase" regex requires the count number and the word "tools?" to appear within 40 characters of each other on the same line.
- All four target files must exist at the hardcoded paths; the script will raise `FileNotFoundError` if any are missing.

## Callers and integration

Not referenced by any other file in the repo. No CI configuration found that invokes it. Has a proper `raise SystemExit(main())` guard. Appears to be a manual developer check — run it after adding or removing MCP tools. Could be wired into a pre-commit hook or CI step.

## Known gaps / open questions

- No callers found — is this script known to exist? Should it be added to a CI check or pre-commit hook?
- The count phrase check is fragile: if documentation uses "30 tools" but the phrase spans a line break, the check fails silently (returns no error for that file since no match = error, not a false pass).
- Does not check `README.md`, which also lists tool counts according to grep results.
