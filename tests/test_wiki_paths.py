"""
test_wiki_paths.py — wiki.load_wiki_page must stay inside the wiki directory.

The slug is caller-supplied and ``WikiGetInput`` only requires ``min_length=1``,
so it reaches the loader unconstrained from both ``nova_wiki_get`` and the
``nova://wiki/{slug}`` template. ``shard_format.load_shard_file`` has always
resolved and bounds-checked its id; this loader did not, so
``load_wiki_page("../secret")`` read a file outside the wiki.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import wiki


@pytest.fixture()
def wiki_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(wiki, "_WIKI_DIR", root)
    return root


def _page(title: str) -> str:
    return f"---\ntitle: {title}\ntags: []\nupdated: 2026-01-01T00:00:00+00:00\nsources: []\n---\n\nBody of {title}.\n"


def test_a_page_inside_the_wiki_loads(wiki_root):
    (wiki_root / "real.md").write_text(_page("Real"), encoding="utf-8")
    page = wiki.load_wiki_page("real")
    assert page is not None
    assert page.slug == "real"


@pytest.mark.parametrize("slug", [
    "../secret",
    "../../secret",
    "sub/../../secret",
])
def test_a_slug_escaping_the_wiki_is_refused(wiki_root, slug):
    (wiki_root.parent / "secret.md").write_text(_page("Secret"), encoding="utf-8")
    assert wiki.load_wiki_page(slug) is None


def test_an_absolute_slug_is_refused(wiki_root, tmp_path):
    (tmp_path / "elsewhere.md").write_text(_page("Elsewhere"), encoding="utf-8")
    assert wiki.load_wiki_page(str(tmp_path / "elsewhere")) is None
