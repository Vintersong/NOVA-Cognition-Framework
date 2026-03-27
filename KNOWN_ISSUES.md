# Known Issues

## Open

| # | Date | Component | Description |
|---|------|-----------|-------------|
| 1 | 2026-03-26 | Stitch MCP | All Stitch tool calls time out (`create_project`, `list_projects`). Likely missing API token or auth config. No workaround currently. |
| 2 | 2026-03-26 | NOVA `nova_shard_create` | Shard creation times out. Suspected cause: post-write `enrich_shard` embedding hook stalls on first cold run. `nova_shard_update` on existing shards works fine. |

## Resolved

| # | Date | Component | Description | Fix |
|---|------|-----------|-------------|-----|
| 3 | 2026-03-26 | NOVA MCP startup | `ModuleNotFoundError: No module named 'filelock'` — server failed to start after code change introduced new dependency. | Ran `pip install -r mcp/requirements.txt` in Python 3.12 env. |
