#!/bin/sh
set -e

DATA=/app/data

# Ensure runtime dirs exist (in case volume was freshly mounted)
mkdir -p "$DATA/shards" "$DATA/nova_sessions" "$DATA/output/forgemaster_runs" \
         "$DATA/intake" "$DATA/wiki" "$DATA/facts"

# Seed dummy shard on first boot so the server is immediately queryable
SHARDS="$DATA/shards"
if [ -z "$(ls -A "$SHARDS"/*.json 2>/dev/null)" ]; then
    echo "[nova] First boot — seeding dummy health-check shard"
    cp /app/docker/seed/nova_docker_healthcheck.json "$SHARDS/nova_docker_healthcheck.json"
fi

echo "[nova] Starting NOVA MCP server"
exec python /app/mcp/nova_server.py
