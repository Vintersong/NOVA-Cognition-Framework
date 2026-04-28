"""
nova_embeddings_local.py — Local embedding backend for nova_server.py

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

import threading
from datetime import datetime

# ═══════════════════════════════════════════════════════════
# LOCAL EMBEDDING MODEL
# ═══════════════════════════════════════════════════════════

_embedding_model = None
_model_lock = threading.Lock()

def get_embedding_model():
    """
    Load the sentence-transformers model.
    Thread-safe. Only loads once per server session.
    """
    global _embedding_model
    with _model_lock:
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


def prewarm_embedding_model() -> None:
    """
    Start loading the embedding model in a background daemon thread at server startup.
    Returns immediately — model loads in the background so the first shard
    operation never blocks waiting for weights to load.
    """
    def _load():
        print("[NOVA] Pre-warming embedding model in background...")
        get_embedding_model()
        print("[NOVA] Embedding model ready.")

    t = threading.Thread(target=_load, daemon=True, name="nova-embed-prewarm")
    t.start()


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
    Structured compaction summary.
    Goal/Progress/Decisions/Next-Steps template borrowed from
    hermes-agent ContextCompressor. No LLM required — heuristic extraction.
    """
    if not turns:
        return "Empty conversation."

    user_messages = [t.get("user", "").strip() for t in turns if t.get("user", "").strip()]
    ai_messages = [t.get("ai", "").strip() for t in turns if t.get("ai", "").strip()]

    if not user_messages:
        return "Conversation with no user messages."

    # [GOAL] — opening intent captured from first user message
    goal = user_messages[0][:200]

    # [PROGRESS] — turn count + last user request
    final_user = user_messages[-1][:120] if len(user_messages) > 1 else ""
    progress = (
        f"{len(turns)} turns. Last request: {final_user}"
        if final_user else f"{len(turns)} turns."
    )

    # [DECISIONS] — scan AI responses for decision-bearing sentences
    decision_keywords = (
        "decided", "will use", "using", "chosen", "approach",
        "implement", "solution", "going with", "selected",
    )
    decisions: list[str] = []
    for msg in ai_messages:
        lower = msg.lower()
        for kw in decision_keywords:
            if kw in lower:
                for sentence in msg.split(". "):
                    if kw in sentence.lower():
                        decisions.append(sentence.strip()[:120])
                        break
                break
        if len(decisions) >= 3:
            break

    # [NEXT] — first sentence of the last AI message
    next_step = ""
    if ai_messages:
        first_sentence = ai_messages[-1].split(". ")[0].strip()[:120]
        if len(first_sentence) > 10:
            next_step = first_sentence

    parts = [
        f"[GOAL] {goal}",
        f"[PROGRESS] {progress}",
    ]
    if decisions:
        parts.append("[DECISIONS] " + " | ".join(decisions))
    if next_step:
        parts.append(f"[NEXT] {next_step}")

    return "\n".join(parts)


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
        shard_data.setdefault("meta_tags", {})["enrichment_status"] = "pending"
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
            "last_context_update": datetime.now().isoformat(),
            "embedding_model": "all-MiniLM-L6-v2"
        }
        shard_data["meta_tags"]["enrichment_status"] = "enriched_local"

    except Exception as e:
        shard_data.setdefault("meta_tags", {})["enrichment_status"] = f"failed: {str(e)[:50]}"


def _generate_compaction_summary(turns: list[dict], shard_id: str) -> str:
    """Generate compaction summary without any API."""
    return generate_local_summary(turns, shard_id)
