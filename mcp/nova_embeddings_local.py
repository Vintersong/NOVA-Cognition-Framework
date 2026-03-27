"""
nova_embeddings_local.py — Local embedding backend for nova_server_v2.py

Provides enrich_shard and _generate_compaction_summary using local sentence-transformers.
No API key required. Fully local and offline after first run.

Install:
    pip install sentence-transformers

First run downloads ~80MB model to ~/.cache/huggingface/
All subsequent runs are fully offline.

Model: all-MiniLM-L6-v2
  - 80MB, fast, good quality for semantic similarity
  - 384-dimensional embeddings
  - Runs on CPU, no GPU required
  - Apache 2.0 license
"""

# ═══════════════════════════════════════════════════════════
# LOCAL EMBEDDING MODEL
# ═══════════════════════════════════════════════════════════

_embedding_model = None

def get_embedding_model():
    """
    Lazy-load the sentence-transformers model.
    Only loads once per server session.
    """
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
            print("[OK] Local embedding model loaded (all-MiniLM-L6-v2)")
        except ImportError:
            print("[WARN] sentence-transformers not installed. Run: pip install sentence-transformers")
            print("  Falling back to keyword-only search.")
            _embedding_model = None
    return _embedding_model


def generate_local_embedding(text: str) -> list[float] | None:
    """Generate a local embedding vector for the given text. Returns None if model unavailable."""
    model = get_embedding_model()
    if model is None:
        return None
    try:
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()
    except Exception as e:
        print(f"  ⚠ Embedding error: {e}")
        return None


def generate_local_summary(turns: list[dict], shard_id: str) -> str:
    """
    Generate a compaction summary without any API.
    Extracts key phrases from the conversation using simple heuristics.
    Not as good as GPT but zero cost and zero latency.
    """
    if not turns:
        return "Empty conversation."

    # Collect user messages (they carry the intent)
    user_messages = [t.get("user", "") for t in turns if t.get("user")]

    if not user_messages:
        return "Conversation with no user messages."

    # Take first, middle, and last user message as summary anchors
    anchors = []
    if len(user_messages) >= 1:
        anchors.append(user_messages[0][:120])
    if len(user_messages) >= 3:
        mid = len(user_messages) // 2
        anchors.append(user_messages[mid][:120])
    if len(user_messages) >= 2:
        anchors.append(user_messages[-1][:120])

    anchor_text = " → ".join(anchors)
    return f"Conversation covering: {anchor_text} ({len(turns)} turns compacted)"


# ═══════════════════════════════════════════════════════════
# REPLACEMENT FUNCTIONS — paste these into nova_server_v2.py
# ═══════════════════════════════════════════════════════════

def enrich_shard(shard_id: str, shard_data: dict):
    """
    Post-write hook: generate local embedding + basic context tags.
    No API key required. Uses sentence-transformers locally.
    Blocking — refactor to async post-write hook in future iteration.
    """
    model = get_embedding_model()

    # Build text to embed — guiding question + recent messages
    guiding_question = shard_data.get("guiding_question", "")
    recent_history = shard_data.get("conversation_history", [])[-5:]
    recent_text = " ".join([
        t.get("user", "") + " " + t.get("ai", "")
        for t in recent_history
    ]).strip()

    embed_text = guiding_question + " " + recent_text

    if model is None:
        shard_data.setdefault("meta_tags", {})["enrichment_status"] = "pending_no_model"
        return

    try:
        embedding = generate_local_embedding(embed_text)

        # Generate basic topic tags from the guiding question
        # Simple keyword extraction — no LLM needed
        import re
        words = re.findall(r'\b[a-zA-Z]{4,}\b', embed_text.lower())
        stopwords = {'that', 'this', 'with', 'have', 'will', 'from', 'they',
                     'been', 'were', 'when', 'what', 'where', 'which', 'there',
                     'their', 'about', 'would', 'could', 'should', 'into', 'then',
                     'than', 'some', 'more', 'also', 'just', 'your', 'like'}
        keywords = list(dict.fromkeys([w for w in words if w not in stopwords]))[:6]

        shard_data["context"] = {
            "summary": guiding_question,  # use guiding question as summary
            "topics": keywords,
            "conversation_type": shard_data.get("meta_tags", {}).get("intent", "general"),
            "embedding": embedding,
            "last_context_update": __import__('datetime').datetime.now().isoformat(),
            "embedding_model": "all-MiniLM-L6-v2"
        }
        shard_data["meta_tags"]["enrichment_status"] = "enriched_local"

    except Exception as e:
        shard_data.setdefault("meta_tags", {})["enrichment_status"] = f"failed: {str(e)[:50]}"


def _generate_compaction_summary(turns: list[dict], shard_id: str) -> str:
    """
    Generate compaction summary without any API.
    """
    return generate_local_summary(turns, shard_id)


# ═══════════════════════════════════════════════════════════
# UPDATED REQUIREMENTS
# ═══════════════════════════════════════════════════════════

REQUIREMENTS = """
# NOVA v2 MCP Server dependencies — local embeddings, no API key required
mcp[cli]>=1.0.0
pydantic>=2.0.0
sentence-transformers>=2.2.0
python-dotenv>=1.0.0
# openai is no longer required
"""


# ═══════════════════════════════════════════════════════════
# BATCH ENRICHMENT SCRIPT
# ═══════════════════════════════════════════════════════════

BATCH_ENRICHMENT_SCRIPT = """
# Run this once to enrich all existing shards with local embeddings
# After running, merge suggestions will work in nova_shard_consolidate

import os
import json
from pathlib import Path

SHARD_DIR = os.environ.get("NOVA_SHARD_DIR", "shards")

# Import the local functions
from nova_embeddings_local import enrich_shard

shards = list(Path(SHARD_DIR).glob("*.json"))
print(f"Enriching {len(shards)} shards...")

enriched = 0
for i, fpath in enumerate(shards, 1):
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Skip already enriched with local model
        if data.get("meta_tags", {}).get("enrichment_status") == "enriched_local":
            continue
            
        enrich_shard(data["shard_id"], data)
        
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        enriched += 1
        if enriched % 50 == 0:
            print(f"  Progress: {enriched} shards enriched...")
    except Exception as e:
        print(f"  Error on {fpath.name}: {e}")

print(f"Done. {enriched} shards enriched with local embeddings.")
"""
