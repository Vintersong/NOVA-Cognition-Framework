import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

# === Load environment and initialize OpenAI client ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ OPENAI_API_KEY not set in .env")

client = OpenAI(api_key=OPENAI_API_KEY)

# === Setup ===
SHARD_DIR = "shards"
os.makedirs(SHARD_DIR, exist_ok=True)

MODEL_EMBED = "text-embedding-ada-002"
MODEL_GPT = "gpt-4"

def get_shard_files():
    return [f for f in os.listdir(SHARD_DIR) if f.endswith(".json")]

def load_shard(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load {path}: {e}")
        return None

def save_shard(path, shard):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(shard, f, indent=2)

def generate_summary_and_topics(content):
    prompt = (
        "You are NOVA, a memory processor. Analyze the following shard content:\n\n"
        f"{content[:12000]}\n\n"  # truncate defensively
        "Give me:\n"
        "- A 1-2 sentence summary of the shard's purpose\n"
        "- A list of 3-6 topic tags\n"
        "- The conversation type (e.g., debugging, philosophy, design, memory reflection)"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL_GPT,
            messages=[
                {"role": "system", "content": "You are an AI assistant for context analysis."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ GPT summary failure: {e}")
        return None

def get_embedding(text):
    try:
        response = client.embeddings.create(
            model=MODEL_EMBED,
            input=[text]
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"❌ Embedding error: {e}")
        return None

def parse_summary(raw_output):
    lines = raw_output.strip().splitlines()
    summary, tags, convo_type = "", [], ""
    for line in lines:
        line = line.strip("-• ").strip()
        if line.lower().startswith("a 1") or "summary" in line.lower():
            continue
        elif line.lower().startswith("a list") or "tags" in line.lower():
            continue
        elif "conversation type" in line.lower():
            continue
        elif not summary:
            summary = line
        elif not tags and "," in line:
            tags = [tag.strip() for tag in line.split(",")]
        elif not convo_type:
            convo_type = line
    return summary, tags, convo_type

def process_shards():
    files = get_shard_files()
    for fname in files:
        fpath = os.path.join(SHARD_DIR, fname)
        shard = load_shard(fpath)
        if not shard:
            continue

        text = json.dumps(shard, ensure_ascii=False)
        print(f"🔍 Processing {fname}...")

        summary_raw = generate_summary_and_topics(text)
        if not summary_raw:
            continue

        summary, tags, convo_type = parse_summary(summary_raw)
        if not summary:
            print(f"⚠️ No valid summary for {fname}. Skipping embedding.")
            continue

        embedding = get_embedding(summary)

        shard["context"] = {
            "summary": summary,
            "topics": tags,
            "conversation_type": convo_type,
            "embedding": embedding,
            "last_context_update": datetime.utcnow().isoformat()
        }

        save_shard(fpath, shard)
        print(f"✅ Updated {fname} with context.")
        time.sleep(1.2)  # rate limit buffer

if __name__ == "__main__":
    process_shards()
