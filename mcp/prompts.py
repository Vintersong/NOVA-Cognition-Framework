"""
prompts.py — NOVA's MCP prompts.

Two groups, registered by ``register_prompts(mcp)``:

**Workflow prompts** — six hand-written prompts for the things people actually
do with NOVA: orient in a project, recall around a shard, write a handoff,
ingest a document, run a sprint, audit confidence. Each is parameterised, and
each names the real tools in the real order, so a client gets a working opening
move rather than a description of one.

**Skill prompts** — one per file in ``forgemaster/skills/``, generated. Those
files have a uniform shape (two ``@@`` header lines, an H1 ``# Skill: <name>``,
then a ``## When to Load`` paragraph that is already a usable description), so
the manifest is derivable rather than hand-maintained.

Only the fourteen *core* skills are exposed. ``forgemaster/library/`` holds
another 218 at ~6 KB each: a prompt menu that long is unusable, and serving it
would be roughly half a million tokens of listing. ``SKILL_LIBRARY.md`` is not
the source either — its path column is inconsistent (core rows are prefixed
``NOVA-Cognition-Framework/``, library rows are not) despite its own header
claiming otherwise, so the directory is read directly.

``skill_manifest.parse_skill_manifest`` extracts only ``@@verification`` and
``@@capabilities``, which is the gate's business; the title and description
wanted here come from :func:`read_skill_summary` below.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "forgemaster" / "skills"

_H1_RE = re.compile(r"^#\s+Skill:\s*(.+?)\s*$", re.MULTILINE)
_WHEN_RE = re.compile(r"^##\s+When to Load\s*$\n(.+?)(?=\n\s*\n|\n##\s)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class SkillSummary:
    """What a prompt needs to know about a skill file."""

    name: str
    title: str
    description: str
    path: Path

    def body(self) -> str:
        """The skill file, minus the ``@@`` gate directives.

        Those two lines are for ``capability_gate``, not for a model reading the
        skill, and leaving them in has them read as instructions.
        """
        text = self.path.read_text(encoding="utf-8")
        return re.sub(r"^@@\w+:.*\n", "", text, flags=re.MULTILINE).lstrip()


#: Words that ``str.title()`` gets wrong. The skill filenames are the only
#: source for a title, so these are fixed up rather than hand-writing fourteen.
_TITLE_FIXUPS = {"Qa": "QA", "Nova": "NOVA"}


def _humanise(stem: str) -> str:
    """``forgemaster-qa-review`` -> ``QA Review``."""
    words = stem.removeprefix("forgemaster-").replace("-", " ").title().split()
    return " ".join(_TITLE_FIXUPS.get(w, w) for w in words)


def read_skill_summary(path: Path) -> SkillSummary:
    """Title and one-paragraph description for one skill file."""
    head = path.read_text(encoding="utf-8")[:2000]
    h1 = _H1_RE.search(head)
    when = _WHEN_RE.search(head)
    description = " ".join((when.group(1) if when else "").split())
    # The H1 is "# Skill: <stem>", so it names the skill rather than titling it;
    # it is read anyway so that a file whose H1 disagrees with its filename
    # titles from the H1.
    stem = h1.group(1) if h1 else path.stem
    return SkillSummary(
        name=path.stem,
        title=_humanise(stem),
        description=description or f"The {path.stem} skill.",
        path=path,
    )


def discover_skills(skills_dir: Path | None = None) -> list[SkillSummary]:
    """Every core skill, sorted by name. A file that cannot be read is skipped
    rather than taking the whole prompt list down with it."""
    root = skills_dir or SKILLS_DIR
    if not root.is_dir():
        return []
    summaries = []
    for path in sorted(root.glob("*.md")):
        try:
            summaries.append(read_skill_summary(path))
        except Exception as exc:  # pragma: no cover — a corrupt skill file
            _logger.warning("prompts: could not read skill %s: %s", path.name, exc)
    return summaries


# ── Workflow prompts ─────────────────────────────────────────────────────────

def register_prompts(mcp) -> None:
    """Register the workflow prompts and one prompt per core skill."""

    @mcp.prompt(
        name="nova-orient",
        title="Orient in a Project",
        description=(
            "Load what NOVA already knows about a project and summarise the "
            "current state before doing any work."
        ),
    )
    def nova_orient(project: str) -> str:
        return (
            f"Orient me in the project '{project}' using NOVA's memory. In order:\n\n"
            f"1. `nova_shard_interact(message=\"{project} current state\")` — this is "
            "the entry point; it auto-selects shards by relevance and confidence.\n"
            f"2. For each shard it returns, `nova_graph_query(source=<shard_id>)` to "
            "see what it depends on, extends or contradicts.\n"
            "3. `nova_shard_query_state(stats_only=true)` for the corpus's overall "
            "epistemic health.\n\n"
            "Then tell me, in this order: what the project is, what state it is in, "
            "what was decided and why, and what the open questions are. Quote the "
            "shard ids you relied on. Say plainly which parts you could not find "
            "rather than filling the gap — a memory system that invents continuity "
            "is worse than one that admits a hole."
        )

    @mcp.prompt(
        name="nova-recall",
        title="Recall Around a Shard",
        description="Pull one shard and everything the graph connects it to.",
    )
    def nova_recall(shard_id: str, depth: str = "2") -> str:
        return (
            f"Recall everything NOVA holds around the shard '{shard_id}'.\n\n"
            f"1. `nova_shard_get(shard_id=\"{shard_id}\")` for the full record.\n"
            f"2. `nova_graph_query(source=\"{shard_id}\", transitive=true, "
            f"max_depth={depth})` for what it reaches.\n"
            f"3. `nova_graph_query(target=\"{shard_id}\", transitive=true, "
            f"max_depth={depth})` for what reaches it.\n"
            "4. `nova_shard_get_full` on any neighbour whose guiding question "
            "looks load-bearing.\n\n"
            "Report the shard's own claim, then the claims around it, and call out "
            "any `contradicts` or `supersedes` edge explicitly — those are the ones "
            "that change what the memory means. Note each shard's confidence; a "
            "0.3 shard and a 0.95 shard are not equal evidence."
        )

    @mcp.prompt(
        name="nova-write-handoff",
        title="Write a Session Handoff",
        description=(
            "Decide whether this session needs a handoff shard, and write one if "
            "it does."
        ),
    )
    def nova_write_handoff(project: str) -> str:
        return (
            f"Consider writing a NOVA handoff for '{project}'.\n\n"
            "First apply the test: if reading `git log` and the PR body next "
            "session would tell me everything you are about to write, do not write "
            "it. A code change that landed in a commit, a bug fix visible in the "
            "diff, a doc edit — the commit message is already the handoff.\n\n"
            "Write one only for: work suspended mid-task with uncommitted state, an "
            "architectural decision whose *reasoning* is not in a commit or ADR, a "
            "constraint you discovered that the next session would have to "
            "rediscover, or context the user has said will resume elsewhere.\n\n"
            "If it passes:\n"
            f"1. `nova_shard_search(query=\"{project}\")` — append to the most "
            "specific existing project shard; create one only if none fits.\n"
            "2. `nova_shard_update` with at most four of: CURRENT STATE, IN "
            "PROGRESS, DECISIONS MADE, NEXT ACTION. Skip any that is empty or "
            "recoverable from git. Cap each at five lines.\n"
            "3. `nova_graph_relate` to wire it to the shards it builds on.\n\n"
            "If it does not pass, say so and write nothing."
        )

    @mcp.prompt(
        name="nova-ingest-document",
        title="Ingest a Document",
        description="Route a document into the shard graph or the wiki.",
    )
    def nova_ingest_document(path: str) -> str:
        return (
            f"Ingest '{path}' into NOVA. Choose the destination first, because the "
            "two are for different things:\n\n"
            "- **Shard graph** (`nidhogg_ingest`) if it is evidence — it should "
            "attach to existing memories as provenance and may corroborate or "
            "contradict them. It matches by embedding similarity and appends a "
            "provenance block to each shard it matches.\n"
            "- **Wiki** (`nova_wiki_ingest`) if it is reference material that "
            "belongs on a curated topic page. Check `nova_wiki_schema(action=\"get\")` "
            "first — ingestion routes only to slugs already in the taxonomy, so a "
            "missing page means adding the spec before ingesting.\n\n"
            "Run it with `dry_run=true` first and show me what it would touch. Both "
            "tools ask for approval before writing. Afterwards, report which shards "
            "or pages changed and whether anything came back as a merge candidate."
        )

    @mcp.prompt(
        name="nova-run-sprint",
        title="Run a Forgemaster Sprint",
        description="Load context, then run the four-turn sprint pipeline.",
    )
    def nova_run_sprint(sprint_id: str, design_doc: str, shard_ids: str = "") -> str:
        context_line = (
            f"Pass shard_ids=\"{shard_ids}\" so the sprint starts with that context."
            if shard_ids else
            "Find the relevant shards with `nova_shard_search` first and pass their "
            "ids as shard_ids — a sprint with no memory repeats decisions."
        )
        return (
            f"Run the Forgemaster sprint '{sprint_id}'.\n\n"
            f"{context_line}\n\n"
            "1. `nova_cache_prewarm` if this will be several API calls against the "
            "same context — pass the returned system_prompt verbatim afterwards, "
            "since its exact bytes decide whether the cache reads or writes.\n"
            f"2. `nova_forgemaster_sprint(sprint_id=\"{sprint_id}\", design_doc=...)` "
            "with this design doc. It asks for approval; it runs orchestrator → "
            "planner → implementer → reviewer.\n\n"
            "DESIGN DOC:\n"
            f"{design_doc}\n\n"
            "When it returns, report the reviewer's verdict line, the file written, "
            "and the biconditional check — an unaccounted change there means the "
            "corpus moved without an audit record, and is worth stopping for."
        )

    @mcp.prompt(
        name="nova-audit-confidence",
        title="Audit Corpus Confidence",
        description=(
            "Find the contradicted, low-confidence and stale shards, and decide "
            "what to do about each."
        ),
    )
    def nova_audit_confidence(min_confidence: str = "0.4") -> str:
        return (
            "Audit NOVA's epistemic health.\n\n"
            "1. `nova_shard_query_state(stats_only=true)` for the distribution.\n"
            "2. `nova_shard_query_state(epistemic=0)` — the contradicted shards. "
            "These matter most: something in the corpus disagrees with itself.\n"
            f"3. `nova_shard_query_state(max_confidence={min_confidence})` — below "
            "this NOVA tags a shard `low_confidence` and drops it from default "
            "search, so these are memories that have quietly stopped being recalled.\n"
            "4. `nova_shard_index(filter_tag=\"stale\")` for what has not been "
            "touched recently.\n\n"
            "For each one worth acting on, recommend exactly one of: **validate** "
            "(`nova_shard_validate` with a real source_type and validator — note it "
            "cannot lower a shard's authority outside an explicit supersession), "
            "**corroborate** (`nova_graph_relate` with relation_type="
            "\"corroborated_by\", the only sanctioned way to raise confidence), "
            "**merge** (`nova_shard_merge` for near-duplicates), or **forget** "
            "(`nova_shard_forget`, with a reason — the content stays on disk for "
            "audit).\n\n"
            "Recommend; do not execute. The write tools ask for approval and the "
            "decision is mine."
        )

    # ── Skill prompts ────────────────────────────────────────────────────────

    for summary in discover_skills():
        _register_skill_prompt(mcp, summary)


def _register_skill_prompt(mcp, summary: SkillSummary) -> None:
    """Register one skill file as a prompt.

    In its own function so each closure captures its own *summary* — a loop body
    closing over the variable would give all fourteen prompts the last skill.
    """

    @mcp.prompt(
        name=summary.name,
        title=summary.title,
        description=summary.description,
    )
    def skill_prompt() -> str:
        return summary.body()
