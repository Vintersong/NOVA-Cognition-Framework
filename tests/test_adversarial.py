"""
test_adversarial.py — Unit tests for Step 10 (adversarial NÓTT pass).

Run: pytest tests/test_adversarial.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta

from adversarial import should_run, stamp_run, _build_prompt, _parse_contradictions


def _graph(last_run=None, last_pass_id=None) -> dict:
    meta = {}
    if last_run:
        meta["last_run"] = last_run
    if last_pass_id:
        meta["last_pass_id"] = last_pass_id
    return {"_adversarial_meta": meta} if meta else {}


def _entry(shard_id, summary="a claim", question="what?", confidence=0.9) -> dict:
    return {
        "shard_id": shard_id,
        "guiding_question": question,
        "confidence": confidence,
        "tags": [],
        "meta": {"summary": summary, "superseded_by": None},
        "context_summary": summary,
    }


class TestShouldRun(unittest.TestCase):

    def test_never_run_triggers(self):
        self.assertTrue(should_run({}))

    def test_fresh_run_skips(self):
        g = _graph(last_run=datetime.now().isoformat())
        self.assertFalse(should_run(g))

    def test_old_run_triggers(self):
        old = (datetime.now() - timedelta(days=8)).isoformat()
        g = _graph(last_run=old)
        self.assertTrue(should_run(g))

    def test_malformed_date_triggers(self):
        g = _graph(last_run="not-a-date")
        self.assertTrue(should_run(g))


class TestStampRun(unittest.TestCase):

    def test_stamps_last_run(self):
        g = {}
        stamp_run(g, "adversarial_20260429T120000")
        self.assertIn("last_run", g["_adversarial_meta"])
        self.assertEqual(g["_adversarial_meta"]["last_pass_id"], "adversarial_20260429T120000")

    def test_overwrites_previous(self):
        g = _graph(last_run="2026-01-01T00:00:00")
        stamp_run(g, "new_pass")
        self.assertNotEqual(g["_adversarial_meta"]["last_run"], "2026-01-01T00:00:00")


class TestBuildPrompt(unittest.TestCase):

    def test_contains_shard_ids(self):
        shards = [_entry("shard_a"), _entry("shard_b")]
        prompt = _build_prompt(shards)
        self.assertIn("shard_a", prompt)
        self.assertIn("shard_b", prompt)

    def test_contains_claim_text(self):
        shards = [_entry("s1", summary="The sky is blue")]
        prompt = _build_prompt(shards)
        self.assertIn("The sky is blue", prompt)

    def test_contains_output_rules(self):
        prompt = _build_prompt([_entry("s1")])
        self.assertIn("CONTRADICTS:", prompt)
        self.assertIn("NO_CONTRADICTIONS", prompt)


class TestParseContradictions(unittest.TestCase):

    def test_parses_valid_line(self):
        response = "CONTRADICTS: shard_a | shard_b | They say opposite things"
        result = _parse_contradictions(response, {"shard_a", "shard_b"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "shard_a")
        self.assertEqual(result[0]["target"], "shard_b")
        self.assertIn("opposite", result[0]["reason"])

    def test_ignores_no_contradictions(self):
        result = _parse_contradictions("NO_CONTRADICTIONS", {"shard_a", "shard_b"})
        self.assertEqual(result, [])

    def test_rejects_hallucinated_ids(self):
        response = "CONTRADICTS: fake_id | shard_b | reason"
        result = _parse_contradictions(response, {"shard_a", "shard_b"})
        self.assertEqual(result, [])

    def test_rejects_self_contradiction(self):
        response = "CONTRADICTS: shard_a | shard_a | reason"
        result = _parse_contradictions(response, {"shard_a"})
        self.assertEqual(result, [])

    def test_ignores_malformed_lines(self):
        response = "CONTRADICTS: only_one_part"
        result = _parse_contradictions(response, {"only_one_part"})
        self.assertEqual(result, [])

    def test_multiple_contradictions(self):
        response = (
            "CONTRADICTS: a | b | reason one\n"
            "CONTRADICTS: c | d | reason two\n"
            "NO_CONTRADICTIONS"
        )
        result = _parse_contradictions(response, {"a", "b", "c", "d"})
        self.assertEqual(len(result), 2)

    def test_reason_with_pipe_in_it(self):
        response = "CONTRADICTS: a | b | they | differ | on this"
        result = _parse_contradictions(response, {"a", "b"})
        self.assertEqual(len(result), 1)
        self.assertIn("differ", result[0]["reason"])

    def test_empty_response(self):
        self.assertEqual(_parse_contradictions("", {"a", "b"}), [])


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2)
    passed = result.result.wasSuccessful()
    total = result.result.testsRun
    failures = len(result.result.failures) + len(result.result.errors)
    print(f"\n{'OK' if passed else 'FAILED'} — {total} tests, {failures} failures")
    sys.exit(0 if passed else 1)
