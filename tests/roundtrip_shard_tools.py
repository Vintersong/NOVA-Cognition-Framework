"""
roundtrip_shard_tools.py — driver for ``test_shard_tools_outputs.py``.

Not a test module (no ``test_`` prefix): pytest must not collect it. It is
executed as a subprocess with ``NOVA_DATA_ROOT`` pointed at a temporary
directory, because NOVA reads every path once at import time from ``config``.
Monkeypatching that after the fact would mean patching a dozen module globals
across store/graph/session_store; a fresh interpreter with the env var set is
both simpler and actually hermetic — the real ``shards/`` is never touched.

Drives the shard tools through the in-process MCP client and prints one JSON
report on stdout for the parent test to assert against.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mcp"))


async def drive() -> dict:
    import nova_server
    from mcp import Client

    report: dict = {"calls": {}}

    async def call(client, name, args, key=None):
        result = await client.call_tool(name, {"params": args})
        payload = (result.structured_content or {}).get("result")
        report["calls"][key or name] = {
            "is_error": result.is_error,
            "has_text": bool(result.content and getattr(result.content[0], "text", "")),
            "payload": payload,
        }
        return payload

    async with Client(nova_server.mcp) as c:
        created = await call(c, "nova_shard_create", {
            "guiding_question": "How does the roundtrip behave?",
            "theme": "testing",
            "intent": "reflection",
            "initial_message": "first turn",
            "source": "user_input",
        })
        shard_id = created["shard_id"]

        await call(c, "nova_shard_get", {"shard_id": shard_id})
        await call(c, "nova_shard_update", {
            "shard_id": shard_id, "user_message": "second", "ai_response": "reply",
        })
        await call(c, "nova_shard_get_full", {"shard_id": shard_id})
        # The shard was created from user_input, so it already sits at
        # authority_validated. A peer_validated event is a downgrade and must be
        # refused outside an explicit supersession; externally_published outranks
        # it and is allowed.
        await call(c, "nova_shard_validate", {
            "shard_id": shard_id,
            "source_type": "peer_validated",
            "validator": "roundtrip",
            "mechanism": "peer_review",
        }, key="validate_downgrade")
        await call(c, "nova_shard_validate", {
            "shard_id": shard_id,
            "source_type": "externally_published",
            "validator": "roundtrip",
            "mechanism": "external_publication",
        })
        await call(c, "nova_shard_search", {"query": "roundtrip behave"})
        await call(c, "nova_shard_index", {})
        await call(c, "nova_shard_summary", {"group_by_theme": True}, key="summary_themed")
        await call(c, "nova_shard_list", {})
        await call(c, "nova_shard_query_state", {})

        # interact with an explicit id, into a named session, so the transcript
        # and token totals are inspectable afterwards.
        message = "load the roundtrip shard"
        await call(c, "nova_shard_interact", {
            "message": message,
            "shard_ids": shard_id,
            "session_id": "roundtrip",
            "auto_select": False,
        })

        second = await call(c, "nova_shard_create", {
            "guiding_question": "A second shard to merge.",
            "theme": "testing",
            "intent": "reflection",
        }, key="create_second")
        await call(c, "nova_shard_merge", {
            "shard_ids": f"{shard_id},{second['shard_id']}",
            "new_theme": "testing",
            "new_guiding_question": "Merged roundtrip shards.",
        })
        await call(c, "nova_shard_archive", {"shard_id": second["shard_id"]})
        await call(c, "nova_shard_forget", {
            "shard_id": second["shard_id"], "reason": "roundtrip cleanup",
        })
        await call(c, "nova_shard_get", {"shard_id": "does_not_exist"}, key="missing")

        # ── Wiki ─────────────────────────────────────────────────────────────
        await call(c, "nova_wiki_schema", {"action": "get"}, key="wiki_schema_empty")
        await call(c, "nova_wiki_schema", {
            "action": "add", "slug": "roundtrip", "title": "Roundtrip",
            "description": "A page for the roundtrip.", "tags": "testing",
            "category": "testing",
        }, key="wiki_add")
        await call(c, "nova_wiki_schema", {
            "action": "add", "slug": "roundtrip", "title": "Roundtrip",
        }, key="wiki_add_duplicate")
        await call(c, "nova_wiki_schema", {"action": "get"}, key="wiki_schema")
        # No CLAUDE_API_KEY in CI, so routing returns nothing and this lands on
        # the "no relevant pages" branch — still the WikiIngested shape.
        await call(c, "nova_wiki_ingest", {
            "source": "some text to file away", "source_name": "roundtrip.txt",
        }, key="wiki_ingest")
        await call(c, "nova_wiki_query", {"query": "roundtrip"}, key="wiki_query")
        await call(c, "nova_wiki_list", {}, key="wiki_list")
        await call(c, "nova_wiki_lint", {}, key="wiki_lint")
        await call(c, "nova_wiki_get", {"slug": "not_a_page"}, key="wiki_missing")
        await call(c, "nova_wiki_schema", {
            "action": "remove", "slug": "roundtrip",
        }, key="wiki_remove")
        await call(c, "nova_wiki_schema", {
            "action": "remove", "slug": "roundtrip",
        }, key="wiki_remove_again")

        # Token accounting is asserted against the *persisted* transcript, which
        # is the string the handler serialised for the session store — not the
        # model the client received.
        session = nova_server.ctx.session_store.get("roundtrip")
        report["session"] = {
            "user_message": message,
            "messages": [m["content"] for m in session.messages],
            "input_tokens": session.usage.input_tokens,
            "output_tokens": session.usage.output_tokens,
            "total_tokens": session.usage.total_tokens,
        }

    return report


async def drive_denied() -> dict:
    """Call every tool named in NOVA_DENIED_TOOLS and report what came back.

    Separate from ``drive`` because the permission context is read from the
    environment at import time, so a denial run needs its own interpreter.
    """
    import nova_server
    from mcp import Client
    import os

    names = [n.strip() for n in os.environ["NOVA_DENIED_TOOLS"].split(",") if n.strip()]
    args = {
        "nova_wiki_schema": {"action": "get"},
        "nova_wiki_ingest": {"source": "x"},
        "nova_wiki_query": {"query": "x"},
        "nova_wiki_get": {"slug": "x"},
        "nova_wiki_list": {},
        "nova_wiki_lint": {},
    }

    report: dict = {"calls": {}}
    async with Client(nova_server.mcp) as c:
        for name in names:
            result = await c.call_tool(name, {"params": args[name]})
            report["calls"][name] = {
                "is_error": result.is_error,
                "has_text": bool(result.content and getattr(result.content[0], "text", "")),
                "payload": (result.structured_content or {}).get("result"),
            }
    return report


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    runner = drive_denied if mode == "denied" else drive
    print("@@REPORT@@" + json.dumps(asyncio.run(runner())))
