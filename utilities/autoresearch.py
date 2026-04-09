"""
Autoresearch Loop
Primary: Gemini 2.5 Flash with Google Search grounding.
Fallback: local model via LM Studio's Anthropic-compatible endpoint.

Usage:
    python autoresearch.py                    # run all topics
    python autoresearch.py --topic qa         # run topics tagged 'qa'
    python autoresearch.py --dry-run          # print topics without running
    python autoresearch.py --backend local    # force LM Studio fallback

Requirements:
    - GEMINI_API_KEY set in .env or environment  (primary)
    - LM Studio running at QWEN_BASE_URL          (fallback)
    - pip install google-genai anthropic

Environment:
    GEMINI_API_KEY  — Gemini primary (required for primary path)
    GEMINI_MODEL    — default: gemini-2.5-flash
    QWEN_BASE_URL   — default: http://127.0.0.1:1234  (fallback)
    QWEN_MODEL      — default: auto-detected from LM Studio
    NOVA_SHARD_DIR  — default: ../shards
"""

import os
import json
import time
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:1234")
NOVA_SHARD_DIR = Path(os.getenv(
    "NOVA_SHARD_DIR",
    Path(__file__).parent.parent / "shards"
))
DELAY_BETWEEN_TOPICS = 3  # seconds between requests

SYSTEM_PROMPT = """You are a senior research analyst. Your task is to produce accurate,
well-structured research findings on technical topics.

Rules:
- Ground your answers in verifiable facts and published research where possible.
- Use Google Search to find current, specific information — do not guess or hallucinate citations.
- Be specific and actionable. Avoid generic advice.
- Format output as structured text (numbered lists, headers) suitable for saving as a knowledge shard.
- If a paper or source is referenced, include its title and where to find it.
"""

# ── Research Topics ───────────────────────────────────────────────────────────
# Add topics here. Each has:
#   tag       — filter key for --topic flag
#   title     — becomes the shard guiding question
#   prompt    — what to ask Gemini
#   theme     — NOVA shard theme

RESEARCH_TOPICS = [
    {
        "tag": "qa",
        "title": "What are the key structural anti-patterns in LLM-generated code that a QA review agent should flag?",
        "prompt": """You are a senior software engineer reviewing AI-generated code quality.

List and explain the most important structural anti-patterns that appear specifically in LLM-generated code,
based on empirical research (e.g. SlopCodeBench, SWE-Bench findings).

Focus on:
1. Structural erosion (complexity mass concentrated in high-complexity functions)
2. Verbosity and duplication patterns
3. Failure modes that compound across iterations
4. Heuristics a code review agent could apply automatically

Be specific and actionable. Format as a numbered list with brief explanations.""",
        "theme": "engineering",
        "intent": "research",
    },
    {
        "tag": "qa",
        "title": "What quantitative thresholds should a QA skill use to flag code for escalation vs passing review?",
        "prompt": """You are designing a QA review skill for an AI coding agent system.

Based on software engineering research and best practices, what specific quantitative thresholds
should be used to decide whether generated code passes review or needs escalation?

Consider:
1. Cyclomatic complexity per function (what number triggers a flag?)
2. Function length in lines
3. Duplication ratio thresholds
4. Test coverage minimums
5. Import/dependency count
6. Any other measurable signals

Give concrete numbers with brief justification for each. These will be used as rules in a QA agent skill file.""",
        "theme": "engineering",
        "intent": "research",
    },
    {
        "tag": "qa",
        "title": "How should a code review agent structure its output as JIRA comments to be actionable?",
        "prompt": """You are designing the output format for an AI code review agent that posts findings as JIRA ticket comments.

Design a comment format that is:
1. Structured enough for the implementing agent to act on without ambiguity
2. Human-readable for async review
3. Prioritized (blocker vs warning vs suggestion)
4. Self-contained (no references to 'above' or 'previously mentioned')

Include:
- A template with field definitions
- An example populated comment for a real code issue
- Rules for what constitutes a blocker vs warning vs suggestion""",
        "theme": "engineering",
        "intent": "research",
    },
    {
        "tag": "routing",
        "title": "How does confidence-based routing work in speculative execution frameworks for LLM agents?",
        "prompt": """Explain how confidence-based routing works in LLM agent systems, specifically:

1. What signals are used to measure confidence in a model's output (logit gaps, entropy, self-consistency)?
2. How does the SpecEyes paper use top-K logit gaps for answer separability gating?
3. How can confidence signals be approximated when you don't have access to logits (e.g. sampling multiple completions)?
4. What threshold values are commonly used in the literature?
5. How would you implement a simple confidence gate for routing between a small local model and a larger API model?

Be specific about implementation details.""",
        "theme": "architecture",
        "intent": "research",
    },
    {
        "tag": "skills",
        "title": "What is the optimal structure for a SKILL.md file that an LLM agent lazy-loads at invocation time?",
        "prompt": """You are designing a skill file format for an AI agent orchestration system.

Skills are markdown files loaded into an LLM's context window when a specific task type is invoked.
The goal is maximum instruction density with minimum token cost.

Design the optimal SKILL.md structure covering:
1. Required sections and their order
2. How to front-load the most critical instructions
3. How to handle conditional logic (if X then Y) concisely
4. Token budget guidelines (how long should a skill file be?)
5. What belongs in a skill file vs what belongs in an AGENT.md
6. How to make skills composable (one skill referencing another)

Include a minimal template.""",
        "theme": "architecture",
        "intent": "research",
    },
    {
        "tag": "slopcode",
        "title": "What does SlopCodeBench reveal about how code quality degrades across agent iterations?",
        "prompt": """Summarize the key findings of the SlopCodeBench paper (arxiv 2603.24755) on coding agent degradation:

1. What are 'structural erosion' and 'verbosity' as defined in the paper?
2. What degradation rates were observed across the 11 models tested?
3. What was the highest checkpoint solve rate and what does that imply?
4. How does agent code compare to human-maintained open source code?
5. Did prompt interventions help and if so by how much?
6. What architectural conclusions can be drawn for building more robust coding agents?

Focus on actionable implications for agent system design.""",
        "theme": "research",
        "intent": "research",
    },
]

# ── Gemini Client (primary) ───────────────────────────────────────────────────

_gemini_client = None


def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


import re as _re

# 2.0 models have free-tier quota=0 (Google migrating to 2.5). Only retry 2.5-flash.
GEMINI_503_RETRIES = 3
GEMINI_503_DELAY   = 30  # seconds — 503 is a demand spike, not a quota issue


def _parse_retry_delay(err_str: str) -> float | None:
    """Extract retryDelay seconds from error message if present."""
    m = _re.search(r"retryDelay['\"]:\s*['\"](\d+)s", err_str)
    return float(m.group(1)) if m else None


def query_gemini(prompt: str) -> str:
    from google.genai import types

    last_err = None
    for attempt in range(1, GEMINI_503_RETRIES + 1):
        try:
            response = get_gemini_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            return response.text
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                delay = _parse_retry_delay(err_str) or GEMINI_503_DELAY
                print(f"  [Gemini] 503 demand spike (attempt {attempt}/{GEMINI_503_RETRIES}), retrying in {delay:.0f}s...")
                time.sleep(delay)
            else:
                # 429 with limit:0 → Google zeroed free quota for this model, no point retrying
                # 404 → model not available in this API version
                # auth errors → fail immediately
                raise
    raise last_err


# ── LM Studio Fallback (local) ────────────────────────────────────────────────

def detect_loaded_model(base_url: str) -> str:
    fallback = os.getenv("QWEN_MODEL", "mistralai/ministral-3-3b")
    try:
        url = base_url.rstrip("/") + "/v1/models"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        models = data.get("data", [])
        if models:
            model_id = models[0]["id"]
            print(f"  [LM Studio] Detected model: {model_id}")
            return model_id
    except Exception as e:
        print(f"  [LM Studio] Model detection failed ({e}), using: {fallback}")
    return fallback


def query_local(prompt: str, model: str) -> str:
    from anthropic import Anthropic
    client = Anthropic(base_url=QWEN_BASE_URL, api_key="lmstudio")
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ── Unified Query ─────────────────────────────────────────────────────────────

def query(prompt: str, backend: str, local_model: str) -> tuple[str, str]:
    """
    Returns (content, backend_used).
    backend: 'gemini' | 'local' | 'auto'
    'auto' tries Gemini first, falls back to local on any error.
    """
    if backend == "local":
        return query_local(prompt, local_model), "local"

    if backend == "gemini":
        return query_gemini(prompt), "gemini"

    # auto — Gemini primary, local fallback
    try:
        return query_gemini(prompt), "gemini"
    except Exception as e:
        print(f"  [Gemini] All retries exhausted ({e})")
        print(f"  [Fallback] Trying LM Studio @ {QWEN_BASE_URL} ...")
        return query_local(prompt, local_model), "local"


# ── NOVA Shard Writer ─────────────────────────────────────────────────────────

def write_shard_direct(topic: dict, content: str, backend: str) -> str:
    """Write shard directly to disk."""
    import uuid
    shard_id = f"autoresearch-{uuid.uuid4().hex[:8]}"
    shard_path = NOVA_SHARD_DIR / f"{shard_id}.json"

    shard = {
        "shard_id": shard_id,
        "guiding_question": topic["title"],
        "theme": topic["theme"],
        "intent": topic["intent"],
        "confidence": 0.7,
        "tags": ["autoresearch", topic["tag"]],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "turns": [
            {
                "role": "user",
                "content": f"[Autoresearch — {backend}] {topic['prompt'][:200]}...",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            {
                "role": "assistant",
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "summary": content[:300] + "..." if len(content) > 300 else content,
        "source": f"autoresearch-{backend}",
    }

    NOVA_SHARD_DIR.mkdir(parents=True, exist_ok=True)
    shard_path.write_text(json.dumps(shard, indent=2, ensure_ascii=False), encoding="utf-8")
    return shard_id


# ── Main Loop ─────────────────────────────────────────────────────────────────

def run(topics: list[dict], dry_run: bool = False, backend: str = "auto"):
    local_model = detect_loaded_model(QWEN_BASE_URL) if backend in ("local", "auto") else "n/a"

    print(f"\n{'='*60}")
    print(f"Autoresearch Loop — {len(topics)} topics")
    if backend == "gemini":
        print(f"Backend: Gemini {GEMINI_MODEL} (Google Search grounding)")
    elif backend == "local":
        print(f"Backend: LM Studio — {local_model} @ {QWEN_BASE_URL}")
    else:
        print(f"Backend: auto (Gemini → LM Studio fallback)")
        print(f"  Primary:  {GEMINI_MODEL}")
        print(f"  Fallback: {local_model} @ {QWEN_BASE_URL}")
    print(f"Shard dir: {NOVA_SHARD_DIR}")
    print(f"{'='*60}\n")

    if dry_run:
        for i, topic in enumerate(topics, 1):
            print(f"[{i}] [{topic['tag']}] {topic['title']}")
        print("\nDry run complete — no requests made.")
        return

    results = []

    for i, topic in enumerate(topics, 1):
        print(f"[{i}/{len(topics)}] {topic['title'][:70]}...")

        try:
            start = time.time()
            content, backend_used = query(topic["prompt"], backend, local_model)
            elapsed = time.time() - start

            shard_id = write_shard_direct(topic, content, backend_used)
            results.append({"topic": topic["title"], "shard_id": shard_id, "status": "ok", "backend": backend_used})
            print(f"  ✓ Done in {elapsed:.1f}s [{backend_used}] → shard: {shard_id}")

        except Exception as e:
            results.append({"topic": topic["title"], "error": str(e), "status": "error"})
            print(f"  ✗ Error: {e}")

        if i < len(topics):
            time.sleep(DELAY_BETWEEN_TOPICS)

    # Summary
    print(f"\n{'='*60}")
    print(f"Complete: {sum(1 for r in results if r['status'] == 'ok')}/{len(results)} succeeded")
    for r in results:
        status = "✓" if r["status"] == "ok" else "✗"
        label = f"[{r['backend']}] " if r.get("backend") else ""
        shard = f"→ {r['shard_id']}" if r.get("shard_id") else f"→ {r.get('error', '')}"
        print(f"  {status} {label}{r['topic'][:55]}... {shard}")

    # Write run log
    log_path = NOVA_SHARD_DIR.parent / "nova_autoresearch.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "backend": backend,
            "results": results,
        }) + "\n")
    print(f"\nRun log: {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NOVA Autoresearch Loop")
    parser.add_argument("--topic", help="Filter by tag (qa, routing, skills, slopcode)")
    parser.add_argument("--dry-run", action="store_true", help="List topics without running")
    parser.add_argument(
        "--backend",
        choices=["auto", "gemini", "local"],
        default="auto",
        help="auto=Gemini with LM Studio fallback, gemini=Gemini only, local=LM Studio only (default: auto)",
    )
    args = parser.parse_args()

    topics = RESEARCH_TOPICS
    if args.topic:
        topics = [t for t in topics if t["tag"] == args.topic]
        if not topics:
            print(f"No topics found with tag '{args.topic}'")
            print(f"Available tags: {sorted(set(t['tag'] for t in RESEARCH_TOPICS))}")
            exit(1)

    run(topics, dry_run=args.dry_run, backend=args.backend)
