# Phase 1 Changes — Permission System & Usage Tracking

## What Was Added

### `mcp/permissions.py` — `ToolPermissionContext`

A frozen dataclass that enforces an opt-in access control layer over the 12
NOVA MCP tools.  When no environment variables are set the default instance
(`ToolPermissionContext.DEFAULT`) contains empty deny sets, so **all tools
remain available** — existing behaviour is fully preserved.

Key API:

| Member | Description |
|---|---|
| `denied_tools: frozenset[str]` | Exact lowercase tool names to block |
| `denied_prefixes: tuple[str, ...]` | Prefix patterns — any tool name that starts with a prefix is blocked |
| `blocks(tool_name) -> bool` | Returns `True` if the tool is blocked |
| `from_iterables(deny_tools, deny_prefixes)` | Constructor from plain lists |
| `ToolPermissionContext.DEFAULT` | Singleton with no restrictions |

### `mcp/models.py` — `UsageSummary`, `ShardRecord`, `PermissionDenial`

Three frozen dataclasses ported from
`claw-code-2-electric-bugaloo/src/models.py` and extended for NOVA:

| Class | Purpose |
|---|---|
| `UsageSummary` | Immutable per-session token tracker. `add_turn(prompt, output)` returns a new instance with word-count estimates added. Exposes `total_tokens` property. |
| `ShardRecord` | Typed Python model of a shard's core metadata fields (`shard_id`, `guiding_question`, `intent`, `theme`, `usage_count`, `last_used`). |
| `PermissionDenial` | Lightweight record of a blocked tool call (`tool_name`, `reason`). |

### Changes to `mcp/nova_server_v2.py`

1. **Imports** — added `from permissions import ToolPermissionContext` and
   `from models import UsageSummary`.

2. **Global permission context** — parsed at startup from env vars:
   ```python
   _permission_context = ToolPermissionContext.from_iterables(
       deny_tools=[...],      # from NOVA_DENIED_TOOLS
       deny_prefixes=[...],   # from NOVA_DENIED_PREFIXES
   )
   ```

3. **`get_permitted_tools(permission_context)`** — helper function that
   returns the subset of the 12 NOVA tool names not blocked by the given
   context (defaults to `_permission_context`).

4. **`_permission_error(tool_name)`** — returns a structured JSON error used
   by all blocked tool calls.

5. **Permission gate in all 12 tool handlers** — each handler begins with:
   ```python
   if _permission_context.blocks("<tool_name>"):
       return _permission_error("<tool_name>")
   ```

6. **Module-level `_session_usage: UsageSummary`** — initialised to
   `UsageSummary()` (zero counts) when the server starts.

7. **`nova_shard_interact` token tracking** — after building the response the
   handler calls:
   ```python
   _session_usage = _session_usage.add_turn(params.message, response_str)
   ```
   The running totals are included in the `log_operation` metadata written to
   `nova_usage.jsonl`.

8. **`nova://usage` resource** — now includes a `session_tokens` key:
   ```json
   {
     "entries": [...],
     "total": 42,
     "session_tokens": {
       "input_tokens": 312,
       "output_tokens": 1840,
       "total_tokens": 2152
     }
   }
   ```

---

## Why Each Change Was Made

### Permission system
NOVA v2 exposes 12 MCP tools with no access control layer.  In multi-user or
multi-agent deployments it is desirable to restrict destructive tools
(`nova_shard_forget`, `nova_shard_archive`) or write tools
(`nova_shard_create`, `nova_shard_update`) for read-only clients without
modifying the server code.  The `ToolPermissionContext` pattern makes this
possible through environment variables alone.

### UsageSummary token tracking
The server already logs every operation to `nova_usage.jsonl` but had no
structured in-process accounting of token consumption across a session.
`UsageSummary.add_turn()` provides a lightweight, dependency-free word-count
estimate that accumulates across all `nova_shard_interact` calls in a single
server run and is surfaced through the existing `nova://usage` resource.

---

## How to Use

### Blocking tools with environment variables

```bash
# Block a single tool by exact name
export NOVA_DENIED_TOOLS="nova_shard_forget"

# Block multiple tools
export NOVA_DENIED_TOOLS="nova_shard_forget,nova_shard_archive"

# Block all write tools by prefix
export NOVA_DENIED_PREFIXES="nova_shard_"

# Block an entire category by prefix
export NOVA_DENIED_PREFIXES="nova_graph_"
```

Both variables accept **comma-separated** values.  Matching is
**case-insensitive**.  If neither variable is set (the default) all 12 tools
remain available.

When a blocked tool is called the response is:
```json
{
  "error": "Tool 'nova_shard_forget' is not permitted in the current permission context."
}
```

### Reading session token totals from `nova://usage`

Fetch the `nova://usage` MCP resource at any point in a session.  The
`session_tokens` field shows the running word-count estimates since the server
started:

```json
{
  "session_tokens": {
    "input_tokens": 312,
    "output_tokens": 1840,
    "total_tokens": 2152
  }
}
```

These counts reset when the server restarts.  Individual operation log entries
in `nova_usage.jsonl` also carry `session_input_tokens`,
`session_output_tokens`, and `session_total_tokens` in their `metadata` field
for each `nova_shard_interact` call.

---

## Source References

| Pattern | Origin |
|---|---|
| `ToolPermissionContext` | `Vintersong/claw-code-2-electric-bugaloo` — `src/permissions.py` |
| `UsageSummary` | `Vintersong/claw-code-2-electric-bugaloo` — `src/models.py` |
| `PermissionDenial` | `Vintersong/claw-code-2-electric-bugaloo` — `src/models.py` |
| Usage in tool registry | `Vintersong/claw-code-2-electric-bugaloo` — `src/tools.py` |
