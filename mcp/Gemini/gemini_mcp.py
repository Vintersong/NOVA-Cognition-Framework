from pydantic import BaseModel, Field, ConfigDict
from google import genai
from typing import Optional
from dotenv import load_dotenv
from pathlib import Path
import os
import json
import sys

# Allow importing config from mcp/ when this module is loaded by nova_server.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GEMINI_MODEL as MODEL
from tool_registry import nova_tool
_client = None
_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# Restrict file access to the repository root (two levels up from mcp/Gemini/)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# Outputs must land inside a dedicated workspace directory
_workspace_env = Path(os.environ.get("GEMINI_OUTPUT_DIR", str(_REPO_ROOT / "workspace"))).resolve()
_WORKSPACE_DIR = _workspace_env if _workspace_env.is_relative_to(_REPO_ROOT) else _REPO_ROOT / "workspace"

# Secret/credential files that must never be read into Gemini context and
# egressed to the API, even though they live under _REPO_ROOT. Being inside the
# repo root is necessary but not sufficient — these are denied on top of it.
_SECRET_BASENAMES: frozenset[str] = frozenset({
    ".env", "shard_index.json", "shard_graph.json",
})
_SECRET_SUFFIXES: tuple[str, ...] = (".key", ".pem", ".p12", ".pfx")


def _is_secret_path(path: Path) -> bool:
    """True if *path* names a credential/secret file that must not be egressed."""
    name = path.name.lower()
    return (
        name in _SECRET_BASENAMES
        or name.startswith(".env")          # .env, .env.local, .env.prod, ...
        or name.endswith(_SECRET_SUFFIXES)
    )


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
    skill_path: Optional[str] = Field(default="", description="Repo-relative path to the skill manifest that authorises this call (e.g. forgemaster/skills/forgemaster-implementation.md). When set, the gate parses @@verification and @@capabilities from this file; otherwise the call runs under the operator-direct manifest.")
    session_id: Optional[str] = Field(default="", description="Sprint or session identifier for audit-log correlation. Defaults to 'gemini_ticket'.")


class LoadFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    filepath: str = Field(..., description="Absolute path to a file to load as context")


def register_gemini_tools(mcp, gate=None, audit_log=None) -> None:
    """Register Gemini worker tools onto an existing FastMCP instance.

    ``gate`` (``CapabilityGate``) and ``audit_log`` (``AuditLog``) are passed
    by ``nova_server.py`` so the worker shares the single skill-verification
    layer of the process. When omitted, file writes proceed without gating
    (useful for direct unit invocation outside the MCP server).
    """

    @nova_tool(
        mcp,
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
                # Resolve the active skill manifest from a dedicated file path
                # (NOT from params.context, which is a mixed shards+code blob
                # and would never parse correctly as a manifest).
                from skill_manifest import SkillManifest, parse_skill_manifest
                from capability_gate import CapabilityDenied, HITLDenied

                active_skill = SkillManifest.OPERATOR_DIRECT
                if params.skill_path:
                    try:
                        skill_file = (_REPO_ROOT / params.skill_path).resolve()
                        if skill_file.is_relative_to(_REPO_ROOT) and skill_file.is_file():
                            active_skill = parse_skill_manifest(
                                params.skill_path,
                                skill_file.read_text(encoding="utf-8"),
                            )
                    except Exception:
                        # Fall back to operator-direct; gate decides what proceeds.
                        active_skill = SkillManifest.OPERATOR_DIRECT

                session_id = params.session_id or "gemini_ticket"
                request_id = None
                if gate is not None:
                    try:
                        request_id = gate.check_capability_tag(
                            "fs.write.irrev",
                            True,  # is_irreversible
                            active_skill,
                            session_id,
                            virtual_tool_name="gemini_execute_ticket",
                            target=params.output_file,
                        )
                    except (CapabilityDenied, HITLDenied) as exc:
                        return json.dumps({"status": "error", "message": f"Capability gate denied: {exc}"})

                output_path = (_WORKSPACE_DIR / params.output_file).resolve()
                if not output_path.is_relative_to(_WORKSPACE_DIR.resolve()):
                    if request_id is not None and audit_log is not None:
                        try:
                            audit_log.log_executed(
                                session_id=session_id,
                                request_id=request_id,
                                tool_name="gemini_execute_ticket",
                                skill_id=active_skill.skill_id,
                                verification=active_skill.verification.value,
                                target=params.output_file,
                                ok=False,
                            )
                        except Exception:
                            pass
                    return json.dumps({"status": "error", "message": "Access denied: output path is outside the allowed workspace directory."})

                write_ok = False
                try:
                    os.makedirs(output_path.parent, exist_ok=True)
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(result)
                    write_ok = True
                    return json.dumps({"status": "success", "saved_to": str(output_path), "code": result})
                finally:
                    if request_id is not None and audit_log is not None:
                        try:
                            audit_log.log_executed(
                                session_id=session_id,
                                request_id=request_id,
                                tool_name="gemini_execute_ticket",
                                skill_id=active_skill.skill_id,
                                verification=active_skill.verification.value,
                                target=params.output_file,
                                ok=write_ok,
                            )
                        except Exception:
                            pass

            return json.dumps({"status": "success", "code": result})

        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    @nova_tool(
        mcp,
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
            if _is_secret_path(resolved):
                return json.dumps({"status": "error", "message": "Access denied: refusing to read a credential/secret file."})
            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read()
            return json.dumps({"status": "success", "filepath": str(resolved), "content": content})
        except FileNotFoundError:
            return json.dumps({"status": "error", "message": f"File not found: {params.filepath}"})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})