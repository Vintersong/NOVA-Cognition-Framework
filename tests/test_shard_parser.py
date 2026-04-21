from __future__ import annotations

from datetime import datetime
from pathlib import Path

from shard_parser import ShardDB, ShardParser


VALID_SHARD = """@@id: shard-1
@@topic: Team Decisions
@@tier: department
@@confidence: 1
@@decay_rate: 0.25
@@links: shard-2, shard-3
@@timestamp: 2026-04-01T00:00:00+00:00
---
# Notes
Some markdown content.
"""


def test_parser_reads_valid_shard(tmp_path: Path) -> None:
    shard_path = tmp_path / "valid.shard"
    shard_path.write_text(VALID_SHARD, encoding="utf-8")

    parsed = ShardParser.parse(shard_path)

    assert parsed["valid"] is True
    assert parsed["id"] == "shard-1"
    assert parsed["topic"] == "Team Decisions"
    assert parsed["tier"] == "department"
    assert parsed["confidence"] == 1
    assert parsed["decay_rate"] == 0.25
    assert parsed["links"] == ["shard-2", "shard-3"]
    assert "markdown content" in parsed["content"]


def test_parser_handles_malformed_file_without_crashing(tmp_path: Path) -> None:
    shard_path = tmp_path / "bad.shard"
    shard_path.write_text(
        """@@id shard-1
@@tier: unknown
@@confidence: maybe
@@decay_rate: nope
@@timestamp: not-a-date
No separator
""",
        encoding="utf-8",
    )

    parsed = ShardParser.parse(shard_path)

    assert parsed["valid"] is False
    assert parsed["errors"]


def test_parser_write_round_trip(tmp_path: Path) -> None:
    shard_path = tmp_path / "written.shard"
    shard = {
        "id": "round-trip",
        "topic": "Round Trip",
        "tier": "personal",
        "confidence": 0,
        "decay_rate": 0.05,
        "links": ["a", "b"],
        "timestamp": "2026-04-10T00:00:00+00:00",
        "content": "Hello **world**",
    }

    assert ShardParser.write(shard_path, shard) is True
    parsed = ShardParser.parse(shard_path)

    assert parsed["valid"] is True
    assert parsed["id"] == "round-trip"
    assert parsed["links"] == ["a", "b"]
    assert parsed["content"] == "Hello **world**"


def test_shard_db_insert_query_and_decay(tmp_path: Path) -> None:
    shard_path = tmp_path / "input.shard"
    shard_path.write_text(VALID_SHARD, encoding="utf-8")

    db_path = tmp_path / "shards.db"
    with ShardDB(db_path) as db:
        assert db.add_file(shard_path) is True

        department_rows = db.query(tier="department")
        assert len(department_rows) == 1
        assert department_rows[0]["id"] == "shard-1"
        assert "Some markdown content" in department_rows[0]["content"]

        assert db.query(topic_keyword="decisions")
        assert not db.query(tier="studio")

        updated = db.decay(now=datetime.fromisoformat("2026-04-06T00:00:00+00:00"))
        assert updated == 1

        decayed = db.query(tier="department")[0]
        assert decayed["confidence"] == 0

        updated = db.decay(now=datetime.fromisoformat("2026-04-10T00:00:00+00:00"))
        assert updated == 1

        decayed = db.query(tier="department")[0]
        assert decayed["confidence"] == -1


def test_shard_db_stores_malformed_files_gracefully(tmp_path: Path) -> None:
    shard_path = tmp_path / "broken.shard"
    shard_path.write_text("@@id: \n---\n", encoding="utf-8")

    db_path = tmp_path / "broken.db"
    with ShardDB(db_path) as db:
        assert db.add_file(shard_path) is True
        rows = db.query(topic_keyword="")

    assert len(rows) == 1
    assert rows[0]["valid"] is False
    assert rows[0]["errors"]


def test_parser_rejects_fractional_confidence(tmp_path: Path) -> None:
    shard_path = tmp_path / "fractional.shard"
    shard_path.write_text(
        """@@id: frac
@@topic: Fractional
@@tier: personal
@@confidence: 0.5
@@decay_rate: 0.25
@@links:
@@timestamp: 2026-04-01T00:00:00+00:00
---
Body
""",
        encoding="utf-8",
    )

    parsed = ShardParser.parse(shard_path)
    assert parsed["valid"] is False
    assert any("Invalid confidence" in err for err in parsed["errors"])


def test_shard_db_reinforce_moves_confidence_up_one_step(tmp_path: Path) -> None:
    db_path = tmp_path / "reinforce.db"
    with ShardDB(db_path) as db:
        assert db.upsert(
            {
                "id": "s1",
                "topic": "t",
                "tier": "personal",
                "confidence": -1,
                "decay_rate": 0.2,
                "links": [],
                "timestamp": "2026-04-01T00:00:00+00:00",
                "content": "c",
                "valid": True,
                "errors": [],
            }
        )

        assert db.reinforce("s1") is True
        assert db.query(topic_keyword="")[0]["confidence"] == 0

        assert db.reinforce("s1") is True
        assert db.query(topic_keyword="")[0]["confidence"] == 1


def test_shard_db_decay_transitions_on_threshold_day(tmp_path: Path) -> None:
    db_path = tmp_path / "threshold.db"
    with ShardDB(db_path) as db:
        assert db.upsert(
            {
                "id": "threshold-confirmed",
                "topic": "t",
                "tier": "personal",
                "confidence": 1,
                "decay_rate": 0.25,
                "links": [],
                "timestamp": "2026-04-01T00:00:00+00:00",
                "content": "c",
                "valid": True,
                "errors": [],
            }
        )
        updated = db.decay(now=datetime.fromisoformat("2026-04-05T00:00:00+00:00"))
        assert updated == 1
        assert db.query(topic_keyword="")[0]["confidence"] == 0

    with ShardDB(db_path) as db:
        assert db.upsert(
            {
                "id": "threshold-neutral",
                "topic": "t2",
                "tier": "personal",
                "confidence": 0,
                "decay_rate": 0.25,
                "links": [],
                "timestamp": "2026-04-01T00:00:00+00:00",
                "content": "c2",
                "valid": True,
                "errors": [],
            }
        )
        updated = db.decay(now=datetime.fromisoformat("2026-04-09T00:00:00+00:00"))
        assert updated == 1
        by_topic = {row["topic"]: row for row in db.query(topic_keyword="")}
        assert by_topic["t2"]["confidence"] == -1


def test_shard_db_decay_rate_zero_never_decays(tmp_path: Path) -> None:
    db_path = tmp_path / "zero-decay.db"
    with ShardDB(db_path) as db:
        assert db.upsert(
            {
                "id": "permanent",
                "topic": "permanent",
                "tier": "personal",
                "confidence": 1,
                "decay_rate": 0.0,
                "links": [],
                "timestamp": "2020-01-01T00:00:00+00:00",
                "content": "c",
                "valid": True,
                "errors": [],
            }
        )
        updated = db.decay(now=datetime.fromisoformat("2030-01-01T00:00:00+00:00"))
        assert updated == 0
        assert db.query(topic_keyword="")[0]["confidence"] == 1
