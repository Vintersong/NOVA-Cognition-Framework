"""
test_prompts.py — the skill-derived prompt manifest.

The fourteen skill prompts are generated from ``forgemaster/skills/``, so the
thing worth testing is the derivation: that a skill file yields a usable title
and description, that the gate directives never reach the model, and that the
loop registers fourteen distinct prompts rather than fourteen copies of the last
one — the classic late-binding closure bug, which a manifest snapshot alone
would not explain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import prompts

SKILL = """\
@@verification: unverified
@@capabilities: *

# Skill: forgemaster-qa-review

## When to Load
Load this skill when the review lane runs after any implementation ticket.

## Role
You are the QA reviewer.
"""


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    (tmp_path / "forgemaster-qa-review.md").write_text(SKILL, encoding="utf-8")
    return tmp_path


def test_summary_is_read_from_the_skill_file(skills_dir):
    summary = prompts.read_skill_summary(skills_dir / "forgemaster-qa-review.md")
    assert summary.name == "forgemaster-qa-review"
    assert summary.title == "QA Review"
    assert summary.description == (
        "Load this skill when the review lane runs after any implementation ticket."
    )


def test_the_body_drops_the_gate_directives(skills_dir):
    body = prompts.read_skill_summary(skills_dir / "forgemaster-qa-review.md").body()
    assert "@@verification" not in body
    assert "@@capabilities" not in body
    assert body.startswith("# Skill: forgemaster-qa-review")


def test_a_skill_without_a_when_to_load_still_gets_a_description(tmp_path):
    (tmp_path / "bare.md").write_text("# Skill: bare\n\nNo sections.\n", encoding="utf-8")
    summary = prompts.read_skill_summary(tmp_path / "bare.md")
    assert summary.description


def test_discover_skips_a_directory_that_does_not_exist(tmp_path):
    assert prompts.discover_skills(tmp_path / "nope") == []


def test_the_real_skills_directory_yields_the_core_skills():
    summaries = prompts.discover_skills()
    names = [s.name for s in summaries]
    assert len(names) == len(set(names))
    assert "forgemaster-orchestrator" in names
    assert all(s.title and s.description for s in summaries)
    # forgemaster/library/ holds 218 more at ~6 KB each; only the core set is
    # exposed, or the prompt menu becomes unusable.
    assert len(summaries) < 30


def test_each_skill_prompt_returns_its_own_body():
    """The registration loop closes over its variable, so a body written inline
    would give every prompt the last skill's text."""
    registered: dict[str, object] = {}

    class _DummyMCP:
        def prompt(self, *, name, title=None, description=None, **_):
            def decorator(fn):
                registered[name] = fn
                return fn
            return decorator

    mcp = _DummyMCP()
    for summary in prompts.discover_skills():
        prompts._register_skill_prompt(mcp, summary)

    bodies = {name: fn() for name, fn in registered.items()}
    assert len(set(bodies.values())) == len(bodies)
    for name, body in bodies.items():
        assert f"# Skill: {name}" in body
