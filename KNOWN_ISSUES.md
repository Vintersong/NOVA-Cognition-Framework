# Known Issues

## Open

| # | Date | Component | Description |
|---|------|-----------|-------------|
| 1 | 2026-03-26 | Stitch MCP | All Stitch tool calls time out (`create_project`, `list_projects`). Likely missing API token or auth config. No workaround currently. |

## Resolved

| # | Date | Component | Description | Fix |
|---|------|-----------|-------------|-----|
| 2 | 2026-04-02 | NOVA `nova_shard_create` | Shard creation timed out. Post-write `enrich_shard` embedding hook blocked the async event loop on cold model load. | Moved all three `enrich_shard` call sites (`nova_shard_create`, `nova_shard_update`, `nova_shard_merge`) to run via `asyncio.get_event_loop().run_in_executor(None, ...)`. |
| 3 | 2026-03-26 | NOVA MCP startup | `ModuleNotFoundError: No module named 'filelock'` — server failed to start after code change introduced new dependency. | Ran `pip install -r mcp/requirements.txt` in Python 3.12 env. |
