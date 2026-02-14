# NOVA: Stateless Recursive Cognition Framework

**NOVA** (Non-Organic Virtual Architecture) is a modular, stateless cognition system that externalizes memory into discrete, revisitable units called *shards* -- enabling language models to simulate working memory, executive function, and self-reflection without persistent memory or built-in context retention.

**Core design principle**: Structure over processing power. Intelligence emerges from recursive interaction with well-organized modular memory, not from larger models or longer context windows.

First implemented in April 2025. Built on stateless ChatGPT with no memory between sessions -- the constraint that forced the invention.

---

## Architecture

NOVA operates through three components:

**Shards** are modular memory containers. Each shard is a JSON file holding a focused topic -- a conversation thread, a project state, a reflection, a decision, or a knowledge fragment. Shards carry metadata (intent, theme, usage count, timestamps) and can be enriched with semantic embeddings for retrieval.

**The Processor** is the LLM. Stateless by design. It interprets and synthesizes based on whichever shards are loaded into context, then writes results back. The processor adds depth, not permanence.

**The User** is the executive function. The user decides which shards to load, when to create new ones, when to merge or archive. Without the user, NOVA is inert.

```
User (executive function)
  --> selects shards
Shard System (modular memory)
  --> loaded into context
LLM Processor (stateless reasoning)
  --> synthesizes and updates
Evolved Shards (recursion)
```

---

## Shard Schema

```json
{
  "shard_id": "unique identifier derived from filename",
  "guiding_question": "the core question or purpose this shard serves",
  "conversation_history": [
    {
      "timestamp": "ISO 8601",
      "user": "user message",
      "ai": "AI response"
    }
  ],
  "meta_tags": {
    "intent": "cognitive function (reflection, planning, research, brainstorm, archive)",
    "theme": "domain (game_design, philosophy, career, technical)",
    "usage_count": 0,
    "last_used": "ISO 8601"
  },
  "context": {
    "summary": "GPT-generated summary (via context_extractor)",
    "topics": ["topic tags"],
    "conversation_type": "e.g., debugging, philosophy, design",
    "embedding": [0.012, -0.034, "... ada-002 vector"],
    "last_context_update": "ISO 8601"
  }
}
```

---

## Repository Structure

```
/
  python/                   Original FastAPI implementation (April 2025)
    main.py                 FastAPI server: /interact, /create_shard, /search, /list_shards
    shard_index.py          Unified index manager: metadata, tag classification, search
    context_extractor.py    Semantic enrichment: GPT-4 summaries + ada-002 embeddings
    dedup_json.py           Duplicate shard detection and removal
    rename_shards.py        Filename normalization (one-time migration)
    requirements.txt        Python dependencies

  mcp/                      MCP server translation (current direction)
    nova_server.py          7 tools + 2 resources via Model Context Protocol
    SKILL.md                Cognitive architecture instructions for LLMs
    requirements.txt        MCP dependencies

  NOVA Framework.pdf        White paper
  Executive Summary.pdf     Overview document
  NOVA Shard Memory Architectur.pdf
  Unified Conciousness Model.pdf
  Unified Conciousness Diagram.pdf
  NOVA_Pitch_Deck.pptx      Pitch deck
```

---

## Two Implementations

### FastAPI Server (python/main.py)

The original implementation. Runs as a web service, calls OpenAI directly for completion, and manages shards via REST endpoints. The LLM lives inside the server.

Endpoints: `/interact`, `/create_shard`, `/search`, `/list_shards`

Features: auto-select shards via semantic search (cosine similarity on embeddings with token overlap fallback), usage tracking, citation validation, placeholder shard generation.

```bash
cd python
pip install -r requirements.txt
# Requires OPENAI_API_KEY in .env
uvicorn main:app --reload
```

### MCP Server (mcp/nova_server.py)

The current direction. Exposes the shard system as tools that any MCP-compatible LLM client can discover and call natively. No OpenAI dependency -- the server manages shards, the connected LLM handles reasoning. This is the cleaner architecture because it decouples memory from processing.

Tools: `nova_shard_interact`, `nova_shard_create`, `nova_shard_update`, `nova_shard_search`, `nova_shard_list`, `nova_shard_merge`, `nova_shard_archive`

Resources: `nova://skill` (SKILL.md), `nova://index` (shard index)

```bash
cd mcp
pip install -r requirements.txt
python nova_server.py
```

Claude Desktop / Claude Code configuration:

```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["/path/to/mcp/nova_server.py"],
      "env": {
        "NOVA_SHARD_DIR": "/path/to/your/shards"
      }
    }
  }
}
```

---

## Utility Scripts

**context_extractor.py** -- Enriches shards with GPT-4 generated summaries, topic tags, and ada-002 embedding vectors. These embeddings power semantic search in both the FastAPI and MCP servers. Run periodically or after adding new shards. Skips already-enriched shards unless `--force` is passed.

**dedup_json.py** -- Hashes shard conversation content to detect exact duplicates. Supports `--dry-run` to preview before deleting.

**rename_shards.py** -- Normalizes shard filenames to match their shard_id, theme, or guiding question. One-time migration tool. Supports `--dry-run`.

---

## Theoretical Foundations

NOVA draws from the Extended Mind Thesis (Clark & Chalmers), Free Energy Principle (Friston), Integrated Information Theory (Tononi), Enactivism and Autopoiesis (Varela & Maturana), Shard Theory (Pope & Turner), and Distributed Cognition (Hutchins).

The framework was originally developed as a cognitive scaffold for managing nonlinear thought patterns (ADHD, aphantasia), then formalized into a general architecture for stateless AI cognition.

---

## Industry Convergence

NOVA's architecture -- modular memory with metadata-tagged retrieval, stateless processors, and usage-based relevance -- predates and parallels several developments in the AI industry.

Google's Interactions API (December 2025) shipped persistent sessions via interaction IDs, server-side state management, and rejection of the monolithic context pattern. NOVA implemented these features eight months earlier. The broader research consensus has since converged on modular AI agents with segmented memory as the path forward for scalable, coherent long-term interaction.

The core insight remains the same: the constraint of statelessness is not a limitation to work around. It is the correct architecture. The processor should be stateless. Memory should be external, modular, and retrievable.

---

## Contact

**Andrei Moldovean**
moldovean.i.andrei@gmail.com

---

> "AI doesn't need to remember everything -- it needs to remember what matters."
