"""
Autoresearch Loop
Primary: Claude (Haiku by default), optionally with Anthropic web search.
Fallback: local model via LM Studio's Anthropic-compatible endpoint.

Usage:
    python autoresearch.py                    # run all topics
    python autoresearch.py --topic qa         # run topics tagged 'qa'
    python autoresearch.py --dry-run          # print topics without running
    python autoresearch.py --backend local    # force LM Studio fallback
    python autoresearch.py --search           # let Claude use web search

Requirements:
    - CLAUDE_API_KEY set in .env or environment  (primary)
    - LM Studio running at QWEN_BASE_URL          (fallback)
    - pip install anthropic

Environment:
    CLAUDE_API_KEY           — Claude primary (required for primary path)
    AUTORESEARCH_MODEL       — default: claude-haiku-4-5-20251001
    AUTORESEARCH_MAX_TOKENS  — default: 4096
    AUTORESEARCH_WEB_SEARCH  — 1 to enable web search without the --search flag
    QWEN_BASE_URL            — default: http://127.0.0.1:1234  (fallback)
    QWEN_MODEL               — default: auto-detected from LM Studio
    NOVA_SHARD_DIR           — default: ../shards

Web search is off by default: an organization admin can disable it in the
Claude Console, and a request that includes the tool then fails with a 400.
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

CLAUDE_MODEL = os.getenv("AUTORESEARCH_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_MAX_TOKENS = int(os.getenv("AUTORESEARCH_MAX_TOKENS", "4096"))
CLAUDE_RETRIES = 3  # SDK-level retries with backoff on 429 / 5xx / overloaded
MAX_PAUSE_CONTINUATIONS = 3  # web search can pause a long turn; resume at most this often
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
USE_WEB_SEARCH = os.getenv("AUTORESEARCH_WEB_SEARCH", "").lower() in ("1", "true", "yes")
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
- When web search is available, use it to find current, specific information. Never guess or invent citations.
- Be specific and actionable. Avoid generic advice.
- Format output as structured text (numbered lists, headers) suitable for saving as a knowledge shard.
- If a paper or source is referenced, include its title and where to find it.
"""

# ── Research Topics ───────────────────────────────────────────────────────────
# Add topics here. Each has:
#   tag       — filter key for --topic flag
#   title     — becomes the shard guiding question
#   prompt    — what to ask the model
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

# ── Claude Client (primary) ───────────────────────────────────────────────────

_claude_client = None


def get_claude_client():
    global _claude_client
    if _claude_client is None:
        from anthropic import Anthropic
        api_key = os.getenv("CLAUDE_API_KEY", "")
        if not api_key:
            raise RuntimeError("CLAUDE_API_KEY not set")
        _claude_client = Anthropic(api_key=api_key, max_retries=CLAUDE_RETRIES)
    return _claude_client


def query_claude(prompt: str, web_search: bool = False) -> str:
    """
    Ask Claude one research question and return the answer text.

    With web_search, only the text after the last search result is kept — the
    text before it is the model announcing that it is about to search. A turn
    that web search pauses is resumed by sending it back unchanged.
    """
    client = get_claude_client()
    messages = [{"role": "user", "content": prompt}]
    kwargs = {"model": CLAUDE_MODEL, "max_tokens": CLAUDE_MAX_TOKENS, "system": SYSTEM_PROMPT}
    if web_search:
        kwargs["tools"] = [WEB_SEARCH_TOOL]

    parts: list[str] = []
    response = None
    for _ in range(MAX_PAUSE_CONTINUATIONS + 1):
        response = client.messages.create(messages=messages, **kwargs)
        for block in response.content:
            block_type = getattr(block, "type", "")
            if block_type == "web_search_tool_result":
                parts = []
            elif block_type == "text":
                parts.append(block.text)
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})

    if response is not None and response.stop_reason == "max_tokens":
        print(f"  [Claude] reply hit max_tokens={CLAUDE_MAX_TOKENS}; output may be cut off")
    return "".join(parts)


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

def query(prompt: str, backend: str, local_model: str, web_search: bool = False) -> tuple[str, str]:
    """
    Returns (content, backend_used).
    backend: 'claude' | 'local' | 'auto'
    'auto' tries Claude first, falls back to local on any error.
    """
    if backend == "local":
        return query_local(prompt, local_model), "local"

    if backend == "claude":
        return query_claude(prompt, web_search), "claude"

    # auto — Claude primary, local fallback
    try:
        return query_claude(prompt, web_search), "claude"
    except Exception as e:
        print(f"  [Claude] request failed ({e})")
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

def run(topics: list[dict], dry_run: bool = False, backend: str = "auto", web_search: bool = False):
    local_model = detect_loaded_model(QWEN_BASE_URL) if backend in ("local", "auto") else "n/a"

    print(f"\n{'='*60}")
    print(f"Autoresearch Loop — {len(topics)} topics")
    search_note = " + web search" if web_search else ""
    if backend == "claude":
        print(f"Backend: Claude {CLAUDE_MODEL}{search_note}")
    elif backend == "local":
        print(f"Backend: LM Studio — {local_model} @ {QWEN_BASE_URL}")
    else:
        print("Backend: auto (Claude → LM Studio fallback)")
        print(f"  Primary:  {CLAUDE_MODEL}{search_note}")
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
            content, backend_used = query(topic["prompt"], backend, local_model, web_search)
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
        choices=["auto", "claude", "local"],
        default="auto",
        help="auto=Claude with LM Studio fallback, claude=Claude only, local=LM Studio only (default: auto)",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Let Claude use Anthropic web search (also: AUTORESEARCH_WEB_SEARCH=1)",
    )
    args = parser.parse_args()

    topics = RESEARCH_TOPICS
    if args.topic:
        topics = [t for t in topics if t["tag"] == args.topic]
        if not topics:
            print(f"No topics found with tag '{args.topic}'")
            print(f"Available tags: {sorted(set(t['tag'] for t in RESEARCH_TOPICS))}")
            exit(1)

    run(topics, dry_run=args.dry_run, backend=args.backend, web_search=args.search or USE_WEB_SEARCH)
