"""
autoresearch_loop/run.py — Karpathy-style autoresearch for NOVA.

Pattern: program.md is the workflow. This script is the executor.
Loop runs indefinitely (Ctrl+C to stop).

Each iteration:
  1. Read program.md
  2. Ask local model to propose the next research query
  3. Execute that query against the local model
  4. Ask local model to score its own output (1-10)
  5. Keep (write NOVA shard) or discard based on score vs threshold
  6. Append to results.tsv
  7. Update program.md with findings
  8. Repeat

Requirements:
    pip install anthropic
    LM Studio running at http://127.0.0.1:1234

Usage:
    python run.py                  # run loop indefinitely
    python run.py --max 20         # stop after 20 iterations
    python run.py --dry-run        # propose first query, don't execute
    python run.py --status         # print results.tsv summary and exit
"""

from __future__ import annotations

import os
import re
import sys
import csv
import json
import time
import uuid
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

try:
    from anthropic import Anthropic
except ImportError:
    print("Missing: pip install anthropic")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────

HERE = Path(__file__).parent
PROGRAM_MD = HERE / "program.md"
RESULTS_TSV = HERE / "results.tsv"
SHARD_DIR = Path(os.getenv(
    "NOVA_SHARD_DIR",
    HERE.parent.parent / "shards"
)) / "autoresearch"

# ── Config ────────────────────────────────────────────────────────────────────

LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234")
KEEP_THRESHOLD = int(os.getenv("AUTORESEARCH_THRESHOLD", "7"))
DELAY_SECONDS = int(os.getenv("AUTORESEARCH_DELAY", "2"))
MAX_ANSWER_TOKENS = 2048
MAX_QUERY_TOKENS = 150
MAX_SCORE_TOKENS = 20


# ── LM Studio helpers ─────────────────────────────────────────────────────────

def detect_model(base_url: str) -> str:
    fallback = os.getenv("LM_MODEL", "mistralai/ministral-3-3b")
    try:
        url = base_url.rstrip("/") + "/v1/models"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        models = data.get("data", [])
        if models:
            return models[0]["id"]
    except Exception:
        pass
    return fallback


def get_client(base_url: str) -> Anthropic:
    return Anthropic(base_url=base_url, api_key="lmstudio")


def call(client: Anthropic, model: str, prompt: str, max_tokens: int) -> str:
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"[ERROR: {e}]"


# ── Results log ───────────────────────────────────────────────────────────────

RESULTS_HEADER = ["timestamp", "iteration", "score", "decision", "query", "shard_id"]


def load_results() -> list[dict]:
    if not RESULTS_TSV.exists():
        return []
    with open(RESULTS_TSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def append_result(row: dict) -> None:
    write_header = not RESULTS_TSV.exists()
    with open(RESULTS_TSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_HEADER, delimiter="\t")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def print_status() -> None:
    rows = load_results()
    if not rows:
        print("No results yet.")
        return
    kept = [r for r in rows if r["decision"] == "KEEP"]
    discarded = [r for r in rows if r["decision"] == "DISCARD"]
    errors = [r for r in rows if r["decision"] == "ERROR"]
    scores = [int(r["score"]) for r in rows if r["score"].isdigit()]
    avg = sum(scores) / len(scores) if scores else 0

    print(f"\n{'='*55}")
    print(f"Autoresearch Results — {len(rows)} iterations")
    print(f"  Kept:      {len(kept)}")
    print(f"  Discarded: {len(discarded)}")
    print(f"  Errors:    {len(errors)}")
    print(f"  Avg score: {avg:.1f} / 10")
    print(f"{'='*55}")
    if kept:
        print("\nKept shards:")
        for r in kept:
            print(f"  [{r['score']}/10] {r['query'][:70]}...")
            print(f"        → shard: {r['shard_id']}")


# ── NOVA shard writer ─────────────────────────────────────────────────────────

def write_shard(query: str, content: str, score: int, model: str) -> str:
    shard_id = f"autoresearch-{uuid.uuid4().hex[:8]}"
    shard_path = SHARD_DIR / f"{shard_id}.json"

    shard = {
        "shard_id": shard_id,
        "guiding_question": query,
        "theme": "research",
        "intent": "research",
        "confidence": min(0.5 + score * 0.05, 0.95),  # score 7→0.85, 10→0.95
        "tags": ["autoresearch", "karpathy-loop"],
        "meta_tags": {
            "source_model": model,
            "quality_score": score,
            "enrichment_status": "pending",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "conversation_history": [
            {
                "user": query,
                "ai": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "source": "autoresearch-loop",
    }

    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    shard_path.write_text(json.dumps(shard, indent=2, ensure_ascii=False), encoding="utf-8")
    return shard_id


# ── program.md updater ────────────────────────────────────────────────────────

def update_program_md(query: str, score: int, decision: str, summary: str) -> None:
    text = PROGRAM_MD.read_text(encoding="utf-8")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if decision == "KEEP":
        entry = f"- [{timestamp}] (score {score}) {query}\n  → {summary[:120]}"
        text = text.replace(
            "*(populated automatically — summaries of kept shards)*",
            f"*(populated automatically — summaries of kept shards)*\n{entry}"
        )
    else:
        entry = f"- [{timestamp}] (score {score}) {query}"
        text = text.replace(
            "*(populated automatically — what didn't score well and why)*",
            f"*(populated automatically — what didn't score well and why)*\n{entry}"
        )

    PROGRAM_MD.write_text(text, encoding="utf-8")


# ── Core loop ─────────────────────────────────────────────────────────────────

PROPOSE_PROMPT = """You are managing a research loop. Your job is to propose the NEXT research query.

Read this research program document carefully:

{program}

---

Previous queries already run (do NOT repeat these):
{previous_queries}

---

Propose exactly ONE focused research question. Rules:
- Must be from the Active directions in the program
- Must be specific and mechanistic — not "explain X" but "how does X handle Y when Z"
- Must not repeat any previous query
- Output ONLY the question, nothing else. No preamble, no explanation."""


RESEARCH_PROMPT = """Use your web search tool to find current, specific information on this question.
Search for relevant sources, then synthesize a focused answer.

Question: {query}

Requirements:
- Search before answering — do not rely on training memory alone
- Cite specific mechanisms, numbers, or named techniques where available
- Keep the answer under 500 words — dense and actionable, no filler
- If sources conflict, note it briefly"""


EVALUATE_PROMPT = """Rate the quality of this research answer on a scale of 1 to 10.

Rubric:
9-10: Specific, actionable, cites mechanisms or numbers, no filler
7-8: Solid, mostly specific, minor vagueness
5-6: Correct but generic — textbook-level
3-4: Vague, circular, or padding-heavy
1-2: Wrong, incoherent, or refused

Question: {query}

Answer: {answer}

Output ONLY a single integer (1-10). Nothing else."""


def extract_score(raw: str) -> int | None:
    m = re.search(r'\b([1-9]|10)\b', raw)
    return int(m.group(1)) if m else None


def run_loop(max_iterations: int | None, dry_run: bool) -> None:
    client = get_client(LM_STUDIO_URL)
    model = detect_model(LM_STUDIO_URL)

    print(f"\n{'='*55}")
    print(f"NOVA Autoresearch Loop")
    print(f"Model:     {model}")
    print(f"Threshold: {KEEP_THRESHOLD}/10 to keep")
    print(f"Shard dir: {SHARD_DIR}")
    print(f"Max iters: {max_iterations or 'unlimited'}")
    print(f"{'='*55}")
    print("Press Ctrl+C to stop.\n")

    iteration = len(load_results())

    try:
        while True:
            if max_iterations and iteration >= max_iterations:
                print(f"\nReached max iterations ({max_iterations}). Stopping.")
                break

            iteration += 1
            print(f"[{iteration}] Proposing query...", end=" ", flush=True)

            # Step 1: propose next query
            program = PROGRAM_MD.read_text(encoding="utf-8")
            previous = load_results()
            prev_queries = "\n".join(
                f"- {r['query']}" for r in previous[-20:]  # last 20
            ) or "(none yet)"

            raw_query = call(
                client, model,
                PROPOSE_PROMPT.format(program=program, previous_queries=prev_queries),
                MAX_QUERY_TOKENS,
            )

            query = raw_query.strip().strip('"').strip()
            if query.startswith("[ERROR"):
                print(f"Query proposal failed: {query}")
                time.sleep(DELAY_SECONDS)
                continue

            print(f"done.\n  Q: {query[:80]}{'...' if len(query) > 80 else ''}")

            if dry_run:
                print("\nDry run — stopping after first proposal.")
                break

            # Step 2: execute research query
            print(f"  Researching...", end=" ", flush=True)
            t0 = time.time()
            answer = call(client, model, RESEARCH_PROMPT.format(query=query), MAX_ANSWER_TOKENS)
            elapsed = time.time() - t0

            if answer.startswith("[ERROR"):
                print(f"Research call failed: {answer}")
                append_result({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "iteration": iteration,
                    "score": "0",
                    "decision": "ERROR",
                    "query": query,
                    "shard_id": "",
                })
                time.sleep(DELAY_SECONDS)
                continue

            print(f"done ({elapsed:.1f}s, {len(answer)} chars)")

            # Step 3: self-evaluate
            print(f"  Scoring...", end=" ", flush=True)
            raw_score = call(
                client, model,
                EVALUATE_PROMPT.format(query=query, answer=answer),
                MAX_SCORE_TOKENS,
            )
            score = extract_score(raw_score)

            if score is None:
                print(f"Score parse failed (raw: {raw_score!r}), treating as discard.")
                score = 0

            print(f"score = {score}/10")

            # Step 4: keep or discard
            if score >= KEEP_THRESHOLD:
                decision = "KEEP"
                shard_id = write_shard(query, answer, score, model)
                summary = answer[:120].replace("\n", " ")
                update_program_md(query, score, "KEEP", summary)
                print(f"  KEEP → shard: {shard_id}")
            else:
                decision = "DISCARD"
                shard_id = ""
                update_program_md(query, score, "DISCARD", "")
                print(f"  DISCARD (score {score} < threshold {KEEP_THRESHOLD})")

            append_result({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "iteration": iteration,
                "score": str(score),
                "decision": decision,
                "query": query,
                "shard_id": shard_id,
            })

            print()
            time.sleep(DELAY_SECONDS)

    except KeyboardInterrupt:
        print(f"\n\nInterrupted after {iteration} iterations.")

    print_status()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NOVA Autoresearch Loop (Karpathy-style)")
    parser.add_argument("--max", type=int, help="Stop after N iterations")
    parser.add_argument("--dry-run", action="store_true", help="Propose first query only, don't execute")
    parser.add_argument("--status", action="store_true", help="Print results summary and exit")
    args = parser.parse_args()

    if args.status:
        print_status()
        sys.exit(0)

    run_loop(max_iterations=args.max, dry_run=args.dry_run)
