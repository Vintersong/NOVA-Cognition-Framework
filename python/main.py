"""
main.py — NOVA FastAPI Server (Original Implementation, April 2025)

Stateless modular cognition via shard-based memory.
Endpoints: /interact, /create_shard, /search, /list_shards
"""

import os
import re
import json
import difflib
import numpy as np
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI

from shard_index import load_index, update_index, SHARD_DIR

# === Environment & Config ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("✗ OPENAI_API_KEY not set in .env")

client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI(title="NOVA Shard Memory Server", version="0.2.0")
os.makedirs(SHARD_DIR, exist_ok=True)

MAX_FRAGMENTS_PER_SHARD = int(os.environ.get("NOVA_MAX_FRAGMENTS", "10"))
FALLBACK_SHARD_ID = "nova_general_memory"


# === Data Models ===

class MetaTags(BaseModel):
    intent: str
    theme: str


class ShardCreateRequest(BaseModel):
    user_message: str
    ai_response: str = ""
    meta_tags: MetaTags
    guiding_question: str = ""


class ShardInteractRequest(BaseModel):
    shard_ids: List[str] = []
    user_message: str
    system_prompt: Optional[str] = None
    auto_select_shards: Optional[bool] = False
    suggest_missing_shards: Optional[bool] = True
    auto_generate_missing_shards: Optional[bool] = False


class ShardSearchRequest(BaseModel):
    query: str
    top_n: int = 5
    use_semantic: bool = True


# === Shard I/O ===

def sanitize_filename(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    return name[:40]


def get_unique_filename(base: str) -> str:
    filename = base + ".json"
    i = 1
    while os.path.exists(os.path.join(SHARD_DIR, filename)):
        filename = f"{base}_{i}.json"
        i += 1
    return filename


def load_shard(shard_id: str) -> tuple[dict, str]:
    filepath = os.path.join(SHARD_DIR, shard_id + ".json")
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Shard '{shard_id}' not found.")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f), filepath


def save_shard(filepath: str, data: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def update_shard_usage(data: dict):
    """Increment usage counter and update last_used timestamp."""
    meta = data.setdefault("meta_tags", {})
    meta["usage_count"] = meta.get("usage_count", 0) + 1
    meta["last_used"] = datetime.now().isoformat()


def extract_fragment_text(shard_data: dict, shard_id: str) -> List[str]:
    fragments = []
    for entry in shard_data.get("conversation_history", []):
        user = entry.get("user", "")
        ai = entry.get("ai", "")
        if user:
            fragments.append(f"[SHARD: {shard_id}] User said: {user}")
        if ai:
            fragments.append(f"[SHARD: {shard_id}] NOVA replied: {ai}")
    return fragments


def create_placeholder_shard(shard_id: str):
    filepath = os.path.join(SHARD_DIR, f"{shard_id}.json")
    if not os.path.exists(filepath):
        placeholder = {
            "shard_id": shard_id,
            "guiding_question": f"What core principles or knowledge should this shard ({shard_id}) contain?",
            "conversation_history": [],
            "meta_tags": {
                "intent": "placeholder_generation",
                "theme": "auto_memory_patch",
                "usage_count": 0,
                "last_used": datetime.now().isoformat()
            }
        }
        save_shard(filepath, placeholder)


# === Search Logic ===

def cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two embedding vectors."""
    a_arr, b_arr = np.array(a), np.array(b)
    dot = np.dot(a_arr, b_arr)
    norm = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    return float(dot / norm) if norm > 0 else 0.0


def get_query_embedding(text: str) -> list | None:
    """Get embedding for a search query via OpenAI."""
    try:
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=[text]
        )
        return response.data[0].embedding
    except Exception:
        return None


def search_shards_semantic(query: str, index: dict, top_n: int = 5) -> list[dict]:
    """Search shards using embeddings if available, falling back to fuzzy match."""
    query_embedding = get_query_embedding(query)

    results = []
    for shard_id, entry in index.items():
        if "archived" in entry.get("tags", []):
            continue

        score = 0.0
        method = "fuzzy"

        # Try semantic search first if both embeddings exist
        if query_embedding:
            shard_path = os.path.join(SHARD_DIR, entry["filename"])
            shard_data = None
            try:
                with open(shard_path, "r", encoding="utf-8") as f:
                    shard_data = json.load(f)
            except Exception:
                pass

            if shard_data:
                shard_embedding = shard_data.get("context", {}).get("embedding")
                if shard_embedding:
                    score = cosine_similarity(query_embedding, shard_embedding)
                    method = "semantic"

        # Fallback: fuzzy match against guiding question + topics + summary
        if method == "fuzzy":
            searchable = " ".join([
                entry.get("guiding_question", ""),
                entry.get("context_summary", ""),
                " ".join(entry.get("context_topics", [])),
                entry.get("meta", {}).get("theme", ""),
                entry.get("meta", {}).get("intent", ""),
            ]).lower()

            # Simple token overlap scoring
            query_tokens = set(query.lower().split())
            search_tokens = set(searchable.split())
            overlap = query_tokens & search_tokens
            score = len(overlap) / max(len(query_tokens), 1)

        results.append({
            "shard_id": shard_id,
            "guiding_question": entry.get("guiding_question", ""),
            "score": round(score, 4),
            "method": method,
            "tags": entry.get("tags", []),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


def guess_relevant_shards(message: str, index: dict, top_n: int = 3) -> list[str]:
    """Quick relevance guess for auto-select. Uses search_shards_semantic internally."""
    results = search_shards_semantic(message, index, top_n=top_n)
    return [r["shard_id"] for r in results if r["score"] > 0.1]


# === Routes ===

@app.post("/interact")
async def interact(request: ShardInteractRequest):
    shard_blocks = []
    inferred_mode = False

    if not request.shard_ids or request.auto_select_shards:
        inferred_mode = True
        try:
            index = load_index()
            if not index:
                index = update_index()
            guessed = guess_relevant_shards(request.user_message, index)
            request.shard_ids = guessed if guessed else [FALLBACK_SHARD_ID]
        except Exception:
            request.shard_ids = [FALLBACK_SHARD_ID]

    # Ensure fallback shard exists
    if FALLBACK_SHARD_ID in request.shard_ids:
        fallback_path = os.path.join(SHARD_DIR, FALLBACK_SHARD_ID + ".json")
        if not os.path.exists(fallback_path):
            create_placeholder_shard(FALLBACK_SHARD_ID)

    for shard_id in request.shard_ids:
        try:
            shard_data, filepath = load_shard(shard_id)
            update_shard_usage(shard_data)
            save_shard(filepath, shard_data)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Shard '{shard_id}' not found.")

        fragments = extract_fragment_text(shard_data, shard_id)
        fragments = fragments[-MAX_FRAGMENTS_PER_SHARD:]
        for fragment in fragments:
            shard_blocks.append({"role": "system", "content": fragment})

    base_prompt = (
        "You are NOVA, a modular AI with recursive memory. You have access to multiple shards, "
        "each containing factual past conversations, theories, or reflections.\n\n"
        "You may draw reasonable inferences ONLY from content seen in the loaded shards. "
        "Do not cite any shard unless it was explicitly included in the context.\n\n"
        "Always cite your insights like:\n"
        "- '[SHARD: shard_name] indicates...'\n"
        "- 'As seen in the user message from [SHARD: X]...'\n"
        "If you believe a relevant shard is missing, say: "
        "'A shard such as [SHARD: X] might be relevant here but was not found.'\n"
        "Do not fabricate citations. Synthesize meaning only from valid memory blocks."
    )

    system_message = {"role": "system", "content": base_prompt}
    final_user_message = {"role": "user", "content": request.user_message}
    messages = [system_message] + shard_blocks + [final_user_message]

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=messages
        )
        ai_response = completion.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"✗ OpenAI error: {str(e)}")

    # Track cited shards and detect missing references
    cited_shards = set(re.findall(r"\[SHARD: ([^\]]+)\]", ai_response))
    missing_citations = list(cited_shards - set(request.shard_ids))

    if request.auto_generate_missing_shards:
        for shard_id in missing_citations:
            create_placeholder_shard(shard_id)

    # Append to primary shard's conversation history
    primary_shard_id = request.shard_ids[0]
    try:
        shard_data, filepath = load_shard(primary_shard_id)
        shard_data.setdefault("conversation_history", []).append({
            "timestamp": datetime.now().isoformat(),
            "user": request.user_message,
            "ai": ai_response
        })
        save_shard(filepath, shard_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"✗ Error updating shard '{primary_shard_id}': {str(e)}")

    if missing_citations and request.suggest_missing_shards:
        ai_response += (
            f"\n\n⚠ NOVA referenced shard(s) not currently loaded: {missing_citations}. "
            f"These may be worth creating to expand memory."
        )

    return {
        "response": ai_response,
        "referenced_shards": request.shard_ids,
        "inferred_mode": inferred_mode,
        "missing_references": missing_citations
    }


@app.post("/create_shard")
async def create_shard(request: ShardCreateRequest):
    base_name = sanitize_filename(f"{request.meta_tags.theme}_{request.meta_tags.intent}")
    filename = get_unique_filename(base_name)
    filepath = os.path.join(SHARD_DIR, filename)

    shard_data = {
        "shard_id": filename.replace(".json", ""),
        "guiding_question": request.guiding_question or request.user_message,
        "conversation_history": [
            {
                "timestamp": datetime.now().isoformat(),
                "user": request.user_message,
                "ai": request.ai_response or ""
            }
        ],
        "meta_tags": {
            **request.meta_tags.model_dump(),
            "usage_count": 1,
            "last_used": datetime.now().isoformat()
        }
    }

    try:
        save_shard(filepath, shard_data)
        # Rebuild index to include new shard
        update_index()
        return {"message": "✓ Shard created", "shard_id": shard_data["shard_id"], "file": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"✗ Failed to write shard: {str(e)}")


@app.post("/search")
async def search(request: ShardSearchRequest):
    index = load_index()
    if not index:
        index = update_index()

    results = search_shards_semantic(
        request.query, index,
        top_n=request.top_n
    )

    return {
        "query": request.query,
        "results": results,
        "total_shards": len(index)
    }


@app.get("/list_shards")
async def list_shards():
    index = update_index()
    return {
        "total": len(index),
        "shards": list(index.values())
    }


@app.on_event("startup")
async def startup():
    """Rebuild index on server start."""
    update_index()
