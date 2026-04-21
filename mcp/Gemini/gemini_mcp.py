from pydantic import BaseModel, Field, ConfigDict
from google import genai
from typing import Optional
from dotenv import load_dotenv
from pathlib import Path
import os
import json

MODEL = "gemini-2.5-flash"
_client = None
_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# Restrict file access to the repository root (two levels up from mcp/Gemini/)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# Outputs must land inside a dedicated workspace directory
_workspace_env = Path(os.environ.get("GEMINI_OUTPUT_DIR", str(_REPO_ROOT / "workspace"))).resolve()
_WORKSPACE_DIR = _workspace_env if _workspace_env.is_relative_to(_REPO_ROOT) else _REPO_ROOT / "workspace"


def get_client() -> genai.Client:
    """Lazy-initialize the Gemini client so the key is read at first use,
    not at server startup (avoids stale-key issues when .env changes)."""
    global _client
    if _client is None:
        load_dotenv(dotenv_path=_ENV_PATH, override=True)
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set — check mcp/Gemini/.env")
        _client = genai.Client(api_key=key)
    return _client


class ExecuteTicketInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    ticket: str = Field(..., description="Structured task ticket describing what to generate", min_length=10)
    context: Optional[str] = Field(default="", description="Skill file content, NOVA shard context, and any codebase files relevant to this ticket")
    output_file: Optional[str] = Field(default="", description="If provided, save output to this filename in the working directory")


class LoadFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    filepath: str = Field(..., description="Absolute path to a file to load as context")


def register_gemini_tools(mcp) -> None:
    """Register Gemini worker tools onto an existing FastMCP instance.
    Called by nova_server.py so these tools are served by the single NOVA server.
    """

    @mcp.tool(
        name="gemini_execute_ticket",
        annotations={
            "title": "Execute Ticket via Gemini",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gemini_execute_ticket(params: ExecuteTicketInput) -> str:
        """Send a structured ticket to Gemini Flash for code generation.

        Use this when an orchestrator has planned a task and needs a worker agent
        to execute it. The context field carries the injected skill file content
        and NOVA shard memory assembled by the injection layer. Provide the ticket
        with clear requirements and acceptance criteria.
        """
        from permissions import is_blocked, denial_payload
        if is_blocked("gemini_execute_ticket"):
            return denial_payload("gemini_execute_ticket")
        prompt = f"""You are a specialized code generation agent.

{params.context if params.context else "No context provided — execute the ticket as specified, writing self-contained output."}

TICKET:
{params.ticket}

Return ONLY the output requested by the ticket. No explanation unless the ticket explicitly asks for it.
"""
        try:
            response = get_client().models.generate_content(model=MODEL, contents=prompt)
            result = response.text

            # Strip accidental markdown fences from generated code
            import re
            result = re.sub(r'^```[a-zA-Z]*\n', '', result.strip())
            result = re.sub(r'\n```$', '', result)

            if params.output_file:
                output_path = (_WORKSPACE_DIR / params.output_file).resolve()
                if not output_path.is_relative_to(_WORKSPACE_DIR.resolve()):
                    return json.dumps({"status": "error", "message": "Access denied: output path is outside the allowed workspace directory."})
                os.makedirs(output_path.parent, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(result)
                return json.dumps({"status": "success", "saved_to": str(output_path), "code": result})

            return json.dumps({"status": "success", "code": result})

        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    @mcp.tool(
        name="gemini_load_file",
        annotations={
            "title": "Load File as Context",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def gemini_load_file(params: LoadFileInput) -> str:
        """Load a file from disk to use as codebase context for ticket execution.

        Use this before executing a ticket when the generated output needs to
        integrate with existing files in the project codebase. Supports any
        text-based file type.
        """
        from permissions import is_blocked, denial_payload
        if is_blocked("gemini_load_file"):
            return denial_payload("gemini_load_file")
        try:
            resolved = Path(params.filepath).resolve()
            if not resolved.is_relative_to(_REPO_ROOT):
                return json.dumps({"status": "error", "message": "Access denied: path is outside the allowed directory."})
            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read()
            return json.dumps({"status": "success", "filepath": str(resolved), "content": content})
        except FileNotFoundError:
            return json.dumps({"status": "error", "message": f"File not found: {params.filepath}"})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})