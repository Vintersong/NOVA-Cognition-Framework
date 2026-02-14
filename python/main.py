import os
import json
import re
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI
from shard_index import load_shard_index, save_shard_index, update_index_with_missing_shards
import difflib


# === Load environment and initialize OpenAI client ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ OPENAI_API_KEY not set in .env")

client = OpenAI(api_key=OPENAI_API_KEY)

# === Setup ===
app = FastAPI()
SHARD_DIR = "shards"
os.makedirs(SHARD_DIR, exist_ok=True)
MAX_FRAGMENTS_PER_SHARD = 10

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
    shard_ids: List[str]
    user_message: str
    system_prompt: Optional[str] = None
    allow_inference: Optional[bool] = False
    auto_select_shards: Optional[bool] = False
    suggest_missing_shards: Optional[bool] = True
    auto_generate_missing_shards: Optional[bool] = False
def guess_relevant_shards(message: str, index: dict, top_n: int = 3) -> list:
    if "shards" not in index:
        return []

    matches = difflib.get_close_matches(message.lower(), index["shards"], n=top_n, cutoff=0.2)
    return [match.replace(".json", "") for match in matches]
# === Utility Functions ===
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

def load_shard(shard_id: str):
    filepath = os.path.join(SHARD_DIR, shard_id + ".json")
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Shard '{shard_id}' not found.")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f), filepath

def save_shard(filepath: str, data: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

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
        placeholder_data = {
            "shard_id": shard_id,
            "guiding_question": f"What core principles or knowledge should this shard ({shard_id}) contain?",
            "conversation_history": [],
            "meta_tags": {
                "intent": "placeholder_generation",
                "theme": "auto_memory_patch"
            }
        }
        save_shard(filepath, placeholder_data)

# === ROUTES ===
@app.post("/interact")
async def interact(request: ShardInteractRequest):
    shard_blocks = []
    inferred_mode = False
    fallback_shard_id = "nova_general_memory"

    if not request.shard_ids or request.auto_select_shards:
        inferred_mode = True
        try:
            index = load_shard_index()
            guessed = guess_relevant_shards(request.user_message, index)
            request.shard_ids = guessed if guessed else [fallback_shard_id]
        except FileNotFoundError:
            request.shard_ids = [fallback_shard_id]

    if fallback_shard_id in request.shard_ids:
        fallback_path = os.path.join(SHARD_DIR, fallback_shard_id + ".json")
        if not os.path.exists(fallback_path):
            fallback_data = {
                "shard_id": fallback_shard_id,
                "guiding_question": "Fallback/general memory shard for NOVA",
                "conversation_history": [],
                "meta_tags": {"intent": "general_reflection", "theme": "core"}
            }
            save_shard(fallback_path, fallback_data)

    for shard_id in request.shard_ids:
        try:
            shard_data, _ = load_shard(shard_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Shard '{shard_id}' not found.")

        fragments = extract_fragment_text(shard_data, shard_id)
        fragments = fragments[-MAX_FRAGMENTS_PER_SHARD:]
        for fragment in fragments:
            shard_blocks.append({"role": "system", "content": fragment})

    base_prompt = (
        "You are NOVA, a modular AI with recursive memory. You have access to multiple shards, "
        "each containing factual past conversations, theories, or reflections.\n\n"
    )
    base_prompt += (
        "You may draw reasonable inferences ONLY from content seen in the loaded shards. "
        "Do not cite any shard unless it was explicitly included in the context.\n"
    )
    base_prompt += (
        "\nAlways cite your insights like:\n"
        "- '[SHARD: shard_name] indicates...'\n"
        "- 'As seen in the user message from [SHARD: X]...'\n"
        "If you believe a relevant shard is missing, say: 'A shard such as [SHARD: X] might be relevant here but was not found.'\n"
        "You may ask the user if they would like to create this shard now.\n"
        "Do not fabricate citations. Your purpose is to synthesize meaning only from valid memory blocks."
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
        raise HTTPException(status_code=500, detail=f"❌ OpenAI error: {str(e)}")

    cited_shards = set(re.findall(r"\[SHARD: ([^\]]+)\]", ai_response))
    missing_citations = list(cited_shards - set(request.shard_ids))

    if request.auto_generate_missing_shards:
        for shard_id in missing_citations:
            create_placeholder_shard(shard_id)

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
        raise HTTPException(status_code=500, detail=f"❌ Error updating shard '{primary_shard_id}': {str(e)}")

    suggestion = ""
    if missing_citations and request.suggest_missing_shards:
        suggestion = (
            f"\n\n⚠️ NOVA referenced shard(s) not currently loaded: {missing_citations}. "
            f"These may be worth creating to expand her memory. Would you like to generate them now?"
        )
        ai_response += suggestion

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
                "ai": request.ai_response if request.ai_response else ""
            }
        ],
        "meta_tags": request.meta_tags.dict()
    }

    try:
        save_shard(filepath, shard_data)
        return {"message": "✅ Shard created successfully", "shard_file": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"❌ Failed to write shard: {str(e)}")