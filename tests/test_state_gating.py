"""
test_state_gating.py — Unit tests for passes_state_gate() (Step 8).

Run: pytest tests/test_state_gating.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta

from store import passes_state_gate


def _entry(
    superseded_by=None,
    validity_start=None,
    validity_end=None,
    project_context=None,
) -> dict:
    meta: dict = {}
    if superseded_by is not None:
        meta["superseded_by"] = superseded_by
    if validity_start or validity_end:
        meta["validity_window"] = {"start": validity_start, "end": validity_end}
    if project_context is not None:
        meta["project_context"] = project_context
    return {"meta": meta}


class TestPassesStateGate(unittest.TestCase):

    # ── superseded_by ─────────────────────────────────────────────────────────

    def test_superseded_excluded(self):
        e = _entry(superseded_by="newer_shard_id")
        self.assertFalse(passes_state_gate(e))

    def test_not_superseded_passes(self):
        e = _entry()
        self.assertTrue(passes_state_gate(e))

    def test_superseded_by_null_passes(self):
        e = {"meta": {"superseded_by": None}}
        self.assertTrue(passes_state_gate(e))

    # ── validity_window ───────────────────────────────────────────────────────

    def test_expired_window_excluded(self):
        past = (datetime.now() - timedelta(days=10)).isoformat()
        e = _entry(validity_end=past)
        self.assertFalse(passes_state_gate(e))

    def test_future_start_excluded(self):
        future = (datetime.now() + timedelta(days=10)).isoformat()
        e = _entry(validity_start=future)
        self.assertFalse(passes_state_gate(e))

    def test_active_window_passes(self):
        start = (datetime.now() - timedelta(days=1)).isoformat()
        end = (datetime.now() + timedelta(days=1)).isoformat()
        e = _entry(validity_start=start, validity_end=end)
        self.assertTrue(passes_state_gate(e))

    def test_open_ended_window_passes(self):
        start = (datetime.now() - timedelta(days=5)).isoformat()
        e = _entry(validity_start=start)
        self.assertTrue(passes_state_gate(e))

    def test_malformed_validity_date_ignored(self):
        e = {"meta": {"validity_window": {"start": "not-a-date", "end": "also-not"}}}
        self.assertTrue(passes_state_gate(e))

    # ── project_context ───────────────────────────────────────────────────────

    def test_context_mismatch_excluded(self):
        e = _entry(project_context="old_architecture")
        self.assertFalse(passes_state_gate(e, project_context="new_architecture"))

    def test_context_match_passes(self):
        e = _entry(project_context="nova_v2")
        self.assertTrue(passes_state_gate(e, project_context="nova_v2"))

    def test_no_env_context_passes_any_shard_context(self):
        # If NOVA_PROJECT_CONTEXT is unset (""), shard context is irrelevant.
        e = _entry(project_context="some_specific_project")
        self.assertTrue(passes_state_gate(e, project_context=""))

    def test_no_shard_context_passes_any_env(self):
        # Shard with no project_context is accessible from any project.
        e = _entry()
        self.assertTrue(passes_state_gate(e, project_context="nova_v2"))

    def test_list_context_match(self):
        e = {"meta": {"project_context": ["nova_v2", "forgemaster"]}}
        self.assertTrue(passes_state_gate(e, project_context="forgemaster"))

    def test_list_context_mismatch(self):
        e = {"meta": {"project_context": ["nova_v2", "forgemaster"]}}
        self.assertFalse(passes_state_gate(e, project_context="unrelated_project"))

    # ── combined ─────────────────────────────────────────────────────────────

    def test_high_relevance_stale_context_excluded(self):
        # The design-doc example: high-relevance shard about old architecture
        # is excluded when current project is "new_architecture".
        e = _entry(project_context="old_nova_architecture")
        self.assertFalse(passes_state_gate(e, project_context="new_nova_architecture"))

    def test_empty_entry_passes(self):
        self.assertTrue(passes_state_gate({}, project_context="anything"))


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2)
    passed = result.result.wasSuccessful()
    total = result.result.testsRun
    failures = len(result.result.failures) + len(result.result.errors)
    print(f"\n{'OK' if passed else 'FAILED'} — {total} tests, {failures} failures")
    sys.exit(0 if passed else 1)
