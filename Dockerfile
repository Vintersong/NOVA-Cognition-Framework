FROM python:3.11-slim

WORKDIR /app

# Build deps for sentence-transformers (native extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps — torch excluded (experimental ternary_net only; tests skip gracefully)
COPY mcp/requirements.txt ./mcp/requirements.txt
RUN grep -v "^torch" mcp/requirements.txt \
    | pip install --no-cache-dir -r /dev/stdin

# Pre-download the embedding model into the image layer so first boot never blocks
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Server code + skills (forgemaster skills are needed by nova_forgemaster_sprint)
COPY mcp/ ./mcp/
COPY forgemaster/ ./forgemaster/

# Seed data (dummy shard injected on first boot when /app/data/shards/ is empty)
COPY docker/seed/ ./docker/seed/
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN chmod +x ./docker/entrypoint.sh

# All runtime data lives under /app/data — mount this as a named volume to persist
# shards, sessions, index files, and output across container restarts.
RUN mkdir -p /app/data/shards /app/data/nova_sessions /app/data/output/forgemaster_runs \
             /app/data/intake /app/data/wiki /app/data/facts

# NOVA_HITL_BROKER is a fallback broker only. The seven destructive MCP tools
# ask the operator through the client via elicitation, which works fine in a
# container; `policy` covers the paths with no client to ask — forgemaster's
# per-file writes and the Gemini worker.
ENV PYTHONUNBUFFERED=1 \
    NOVA_SHARD_DIR=/app/data/shards \
    NOVA_INDEX_FILE=/app/data/shard_index.json \
    NOVA_GRAPH_FILE=/app/data/shard_graph.json \
    NOVA_USAGE_LOG=/app/data/nova_usage.jsonl \
    NOVA_SESSION_STORE_DIR=/app/data/nova_sessions \
    NOVA_SUMMARY_INDEX_FILE=/app/data/summary_index.json \
    NOVA_SUMMARY_MARKDOWN_FILE=/app/data/summary_index.md \
    NIDHOGG_INTAKE_DIR=/app/data/intake \
    NOVA_WIKI_DIR=/app/data/wiki \
    FORGEMASTER_WRITE_ROOTS=output,intake \
    NOVA_HITL_BROKER=policy

ENTRYPOINT ["./docker/entrypoint.sh"]
