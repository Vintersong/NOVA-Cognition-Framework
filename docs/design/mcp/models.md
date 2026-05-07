# models.py

**One-line purpose:** Defines `UsageSummary`, a frozen dataclass for tracking per-session token counts using word-count estimates.

## Why it exists

Token usage needed to be accumulated across multiple tool calls within a session without mutating shared state. Using a frozen dataclass (same immutable pattern as `NovaSession` in `session_store.py`) means every update produces a new instance — no accidental mutation, easy to reason about in async contexts.

## Key concepts

- **Word-count estimate** — token counts are approximated as word counts via `len(str.split())`. This is a rough proxy (~1.3 tokens per word on average) used for budget tracking, not billing.

## Public surface

- `UsageSummary(input_tokens, output_tokens)` — frozen dataclass; both fields default to 0.
- `total_tokens` — property; sum of input and output.
- `add_turn(prompt, output) → UsageSummary` — returns a new instance with this turn's word counts added.
- `add_turn_exact(input_tokens, output_tokens) → UsageSummary` — returns a new instance using exact token counts from an API response.

## Inputs and outputs

- **Reads/writes:** nothing. Pure in-memory dataclass.

## Invariants and assumptions

- Immutable — `frozen=True`. All state changes return new instances.
- Word-count approximation; not suitable for exact billing.

## Callers and integration

- `session_store.py` — `NovaSession` contains a `UsageSummary` field; `add_message` calls `add_turn`.
- `nova_server.py` — `_session_usage: UsageSummary` module-level singleton tracks session-wide usage.
- Referenced in `forgemaster_runtime.py` via the session's `usage` field.

## Known gaps / open questions

- Word count is not a reliable token estimate — off by 30–50% for code-heavy or multilingual content. Is this acceptable for budget tracking, or should the actual API usage response be used where available?
- ~~`forgemaster_runtime.py` overwrote real API token counts with word estimates via `add_message` — fixed. `run_turn` now calls `add_message_with_usage` for assistant turns, preserving exact counts.~~ (fixed 2026-04-24)

## Changelog

| Date | Change | Why |
|---|---|---|
| 2026-04-24 | Added `add_turn_exact(input_tokens, output_tokens)` to `UsageSummary`. Added `add_message_with_usage` to `NovaSession`. Updated `forgemaster_runtime.run_turn` to use `add_message_with_usage` for assistant turns. | Real API token counts were being overwritten by word-count estimates, making session usage tracking inaccurate. |
