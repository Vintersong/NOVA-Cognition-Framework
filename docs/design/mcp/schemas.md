# schemas.py

**One-line purpose:** Pydantic input models for all 30 NOVA MCP tools, extracted into one file so tool handlers stay thin.

## Why it exists

Before extraction, input validation was scattered across handler functions. Centralising into Pydantic models gives consistent validation, type coercion, and field defaults without duplicating logic in each handler.

## Key concepts

- **`ConfigDict(str_strip_whitespace=True, extra='forbid')`** — all models strip leading/trailing whitespace from strings and reject unknown fields. This is applied uniformly across every model.
- **`SESSION_ID_PATTERN`** — regex imported from `config.py`; applied as a `pattern=` constraint on session ID fields to prevent path traversal via session file names.

## Public surface

One `BaseModel` subclass per tool. Key models:

- `ShardInteractInput` — `shard_ids` (space-sep string), `message`, `auto_select`, `session_id`.
- `ShardCreateInput` — `guiding_question`, `intent`, `theme`, `initial_message`, `related_shards`, `relation_type`.
- `ShardIndexInput` — `filter_tag`, `min_confidence` (float, 0–1), `sort`, `sort_order`, `page`, `per_page`, `group_by_theme`.
- `ShardSearchInput` — `query`, `top_n`, `include_low_confidence`.
- `ForgemasterSprintInput` — `sprint_id`, `design_doc`, `shard_ids`.
- Wiki models: `WikiSchemaInput`, `WikiIngestInput`, `WikiQueryInput`, `WikiGetInput`, `WikiListInput`, `WikiLintInput`.

## Inputs and outputs

- **Reads:** `config.SESSION_ID_PATTERN` at import time.
- **Writes:** nothing — pure validation layer.

## Invariants and assumptions

- `ShardIndexInput.min_confidence` is typed as `Optional[float]` with `ge=0.0, le=1.0`. This constraint is float-based — incompatible with the new discrete `{-1, 0, 1}` model.
- All models use `extra='forbid'` — extra fields will raise a validation error. Tool callers must pass only documented fields.

## Callers and integration

- The handler modules (`shard_tools.py`, `graph_tools.py`, `session_tools.py`, `forgemaster_tools.py`, `wiki_tools.py`, …) import these models for their handler signatures.
- `mcp/test_nova.py` — imports `ShardIndexInput`, `ShardSearchInput`, `ShardGetInput` for the explorer script.

## Known gaps / open questions

- `ShardIndexInput.min_confidence` is a float field (0.0–1.0). After migration to discrete confidence, this filter is either meaningless or needs to become `Literal[-1, 0, 1]`.
- `ShardListInput.limit: int = Field(default=50, ge=1, le=445)` — why 445? The ceiling appears arbitrary.
