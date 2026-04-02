"""
Gemini Worker MCP Server
Forgemaster tool — routes implementation and boilerplate tickets to Gemini 2.5 Flash.
Registered as gemini-worker in .mcp.json.

Tools exposed:
  gemini_execute_ticket  — execute a Forgemaster ticket via Gemini 2.5 Flash
  gemini_load_file       — load a file from disk as codebase context

Confidence threshold routing:
  If the orchestrator confidence score for a ticket is below CONFIDENCE_THRESHOLD,
  the ticket escalates from Gemini Flash to Claude Sonnet automatically.
  Confidence is passed as an optional parameter; if omitted, threshold check is skipped.

Environment variables:
  GEMINI_API_KEY         — required
  GEMINI_MODEL           — default: gemini-2.5-flash
  CONFIDENCE_THRESHOLD   — default: 0.65 (below this, escalate to Sonnet)
"""

import os
import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from google import genai
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.65"))

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set in environment or .env file")

client = genai.Client(api_key=GEMINI_API_KEY)

mcp = FastMCP("gemini-worker")

SYSTEM_PROMPT = """You are a Forgemaster implementation agent powered by Gemini Flash.
Your role: execute clearly-scoped implementation tickets with precision.

Rules:
- Follow the ticket spec exactly. Do not deviate from acceptance criteria.
- Output complete, working code. No placeholders, no TODOs unless specified.
- If the spec is ambiguous, state what assumption you made and why.
- Prefer simple, readable solutions over clever ones.
- Do not add unrequested features.
- Return structured output: code blocks with file paths as headers.
"""


@mcp.tool()
def gemini_execute_ticket(
    ticket: str,
    context: str = "",
    confidence: float = 1.0,
) -> str:
    """
    Execute a Forgemaster ticket via Gemini Flash.

    Parameters:
      ticket     — full ticket spec including type, title, spec, acceptance_criteria
      context    — optional codebase context (file contents, NOVA shard summaries)
      confidence — orchestrator confidence score for this ticket (0.0 - 1.0)
                   if below CONFIDENCE_THRESHOLD, returns escalation signal instead of executing

    Returns:
      Ticket output (code, structured data, etc.) or escalation signal if confidence too low.
    """
    if confidence < CONFIDENCE_THRESHOLD:
        return json.dumps({
            "status": "escalate",
            "reason": f"Confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD:.2f}",
            "escalate_to": "claude-sonnet",
            "ticket": ticket[:200] + "..." if len(ticket) > 200 else ticket,
        })

    prompt_parts = []
    if context:
        prompt_parts.append(f"## Codebase Context\n\n{context}\n\n---\n")
    prompt_parts.append(f"## Ticket\n\n{ticket}")

    full_prompt = "\n".join(prompt_parts)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=full_prompt,
            config={"system_instruction": SYSTEM_PROMPT},
        )
        output = response.text
        # Strip markdown code fences Gemini sometimes wraps output in.
        # Handles ```lua, ```python, ``` etc. at start and ``` at end.
        import re
        output = re.sub(r"^```[a-zA-Z]*\n", "", output.strip())
        output = re.sub(r"\n```$", "", output)
        return json.dumps({
            "status": "complete",
            "model": GEMINI_MODEL,
            "confidence": confidence,
            "output": output,
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
            "ticket": ticket[:200] + "..." if len(ticket) > 200 else ticket,
        })


@mcp.tool()
def gemini_load_file(path: str, max_chars: int = 8000) -> str:
    """
    Load a file from disk to use as codebase context for ticket execution.

    Parameters:
      path      — absolute or relative path to the file
      max_chars — character limit for returned content (default 8000)

    Returns:
      File contents as string, truncated if necessary.
    """
    try:
        p = Path(path)
        if not p.exists():
            return json.dumps({"status": "error", "error": f"File not found: {path}"})
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[TRUNCATED — {len(content) - max_chars} chars omitted]"
        return json.dumps({
            "status": "ok",
            "path": str(p.resolve()),
            "chars": len(content),
            "content": content,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


if __name__ == "__main__":
    mcp.run()
