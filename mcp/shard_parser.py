from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import sqlite3
from typing import Any


class ShardParser:
    """Read and write NOVA .shard files with graceful error handling."""

    REQUIRED_FIELDS = {
        "id",
        "topic",
        "tier",
        "confidence",
        "decay_rate",
        "links",
        "timestamp",
    }
    VALID_TIERS = {"personal", "department", "studio"}
    VALID_CONFIDENCE = {-1, 0, 1}

    @classmethod
    def parse(cls, file_path: str | Path) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": "",
            "topic": "",
            "tier": "personal",
            "confidence": 0,
            "decay_rate": 0.0,
            "links": [],
            "timestamp": "",
            "content": "",
            "valid": True,
            "errors": [],
        }

        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            data["valid"] = False
            data["errors"].append(f"Unable to read file: {exc}")
            return data

        header_lines: list[str] = []
        content_lines: list[str] = []
        in_content = False
        for raw_line in text.splitlines():
            if not in_content and raw_line.strip() == "---":
                in_content = True
                continue
            if in_content:
                content_lines.append(raw_line)
            else:
                header_lines.append(raw_line)

        if not in_content:
            data["valid"] = False
            data["errors"].append("Missing header/content separator '---'.")

        data["content"] = "\n".join(content_lines)

        for line in header_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("@@"):
                data["valid"] = False
                data["errors"].append(f"Invalid header line: {line}")
                continue
            if ":" not in stripped:
                data["valid"] = False
                data["errors"].append(f"Malformed header line: {line}")
                continue
            key, value = stripped[2:].split(":", 1)
            data[key.strip()] = value.strip()

        cls._validate_and_normalize(data)
        return data

    @classmethod
    def _validate_and_normalize(cls, data: dict[str, Any]) -> None:
        for field in cls.REQUIRED_FIELDS:
            if field not in data or (isinstance(data[field], str) and not data[field].strip() and field != "links"):
                data["valid"] = False
                data["errors"].append(f"Missing required field: {field}")

        if data.get("tier") not in cls.VALID_TIERS:
            data["valid"] = False
            data["errors"].append(f"Invalid tier: {data.get('tier')}")
            data["tier"] = "personal"

        try:
            confidence = int(data.get("confidence", 0))
            if confidence not in cls.VALID_CONFIDENCE:
                raise ValueError
            data["confidence"] = confidence
        except (TypeError, ValueError):
            data["valid"] = False
            data["errors"].append(
                f"Invalid confidence: {data.get('confidence')} "
                f"(must be one of {sorted(cls.VALID_CONFIDENCE)})"
            )
            data["confidence"] = 0

        try:
            decay_rate = float(data.get("decay_rate", 0.0))
            if not 0.0 <= decay_rate <= 1.0:
                raise ValueError
            data["decay_rate"] = decay_rate
        except (TypeError, ValueError):
            data["valid"] = False
            data["errors"].append(f"Invalid decay_rate: {data.get('decay_rate')}")
            data["decay_rate"] = 0.0

        links_raw = data.get("links", "")
        if isinstance(links_raw, str):
            data["links"] = [link.strip() for link in links_raw.split(",") if link.strip()]
        elif isinstance(links_raw, list):
            data["links"] = [str(link).strip() for link in links_raw if str(link).strip()]
        else:
            data["valid"] = False
            data["errors"].append("Invalid links format.")
            data["links"] = []

        timestamp = str(data.get("timestamp", "")).strip()
        if timestamp:
            candidate = timestamp.replace("Z", "+00:00")
            try:
                datetime.fromisoformat(candidate)
            except ValueError:
                data["valid"] = False
                data["errors"].append(f"Invalid timestamp: {timestamp}")
        else:
            data["valid"] = False
            data["errors"].append("Missing required field: timestamp")

    @classmethod
    def write(cls, file_path: str | Path, shard: dict[str, Any]) -> bool:
        payload = {
            "id": shard.get("id", ""),
            "topic": shard.get("topic", ""),
            "tier": shard.get("tier", "personal"),
            "confidence": shard.get("confidence", 0),
            "decay_rate": shard.get("decay_rate", 0.0),
            "links": shard.get("links", []),
            "timestamp": shard.get("timestamp", ""),
            "content": shard.get("content", ""),
            "errors": [],
            "valid": True,
        }
        cls._validate_and_normalize(payload)

        links = ", ".join(payload["links"])
        text = (
            f"@@id: {payload['id']}\n"
            f"@@topic: {payload['topic']}\n"
            f"@@tier: {payload['tier']}\n"
            f"@@confidence: {payload['confidence']}\n"
            f"@@decay_rate: {payload['decay_rate']}\n"
            f"@@links: {links}\n"
            f"@@timestamp: {payload['timestamp']}\n"
            "---\n"
            f"{payload['content']}"
        )

        try:
            Path(file_path).write_text(text, encoding="utf-8")
            return True
        except OSError:
            return False


class ShardDB:
    """SQLite index for shard metadata and content."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shards (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                tier TEXT NOT NULL,
                confidence REAL NOT NULL,
                decay_rate REAL NOT NULL,
                links TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                content BLOB NOT NULL,
                malformed INTEGER NOT NULL DEFAULT 0,
                errors TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        self.conn.commit()

    def upsert(self, shard: dict[str, Any]) -> bool:
        try:
            content_blob = str(shard.get("content", "")).encode("utf-8")
            links = ",".join(shard.get("links", []))
            errors = json.dumps(shard.get("errors", []))
            malformed = 0 if shard.get("valid", True) else 1

            self.conn.execute(
                """
                INSERT INTO shards (id, topic, tier, confidence, decay_rate, links, timestamp, content, malformed, errors)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    topic=excluded.topic,
                    tier=excluded.tier,
                    confidence=excluded.confidence,
                    decay_rate=excluded.decay_rate,
                    links=excluded.links,
                    timestamp=excluded.timestamp,
                    content=excluded.content,
                    malformed=excluded.malformed,
                    errors=excluded.errors
                """,
                (
                    str(shard.get("id", "")),
                    str(shard.get("topic", "")),
                    str(shard.get("tier", "personal")),
                    float(shard.get("confidence", 0)),
                    float(shard.get("decay_rate", 0.0)),
                    links,
                    str(shard.get("timestamp", "")),
                    content_blob,
                    malformed,
                    errors,
                ),
            )
            self.conn.commit()
            return True
        except (sqlite3.Error, ValueError, TypeError):
            return False

    def add_file(self, file_path: str | Path) -> bool:
        shard = ShardParser.parse(file_path)
        if not shard.get("id"):
            shard["id"] = Path(file_path).stem
        return self.upsert(shard)

    def decay(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        updated = 0
        try:
            rows = self.conn.execute(
                "SELECT id, confidence, decay_rate, timestamp FROM shards"
            ).fetchall()
        except sqlite3.Error:
            return 0

        for row in rows:
            try:
                ts = str(row["timestamp"] or "").replace("Z", "+00:00")
                shard_time = datetime.fromisoformat(ts)
                days_since = max(0, (now - shard_time).days)
                factor = max(0.0, 1.0 - (float(row["decay_rate"]) * days_since))
                new_confidence = float(row["confidence"]) * factor
                if abs(new_confidence - float(row["confidence"])) > 1e-9:
                    self.conn.execute(
                        "UPDATE shards SET confidence=? WHERE id=?",
                        (new_confidence, row["id"]),
                    )
                    updated += 1
            except (ValueError, TypeError, sqlite3.Error):
                continue

        self.conn.commit()
        return updated

    def query(
        self,
        *,
        tier: str | None = None,
        confidence: int | float | None = None,
        topic_keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, topic, tier, confidence, decay_rate, links, timestamp, content, malformed, errors "
            "FROM shards WHERE 1=1"
        )
        params: list[Any] = []

        if tier:
            sql += " AND tier = ?"
            params.append(tier)

        if confidence is not None:
            sql += " AND confidence = ?"
            params.append(float(confidence))

        if topic_keyword:
            sql += " AND LOWER(topic) LIKE ?"
            params.append(f"%{topic_keyword.lower()}%")

        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []

        result: list[dict[str, Any]] = []
        for row in rows:
            links = [link for link in str(row["links"]).split(",") if link]
            raw_content = row["content"]
            if isinstance(raw_content, memoryview):
                raw_content = raw_content.tobytes()
            if isinstance(raw_content, (bytes, bytearray)):
                content = raw_content.decode("utf-8", errors="replace")
            else:
                content = str(raw_content)
            try:
                errors = json.loads(row["errors"])
                if not isinstance(errors, list):
                    errors = []
            except (json.JSONDecodeError, TypeError):
                errors = []

            result.append(
                {
                    "id": row["id"],
                    "topic": row["topic"],
                    "tier": row["tier"],
                    "confidence": row["confidence"],
                    "decay_rate": row["decay_rate"],
                    "links": links,
                    "timestamp": row["timestamp"],
                    "content": content,
                    "valid": row["malformed"] == 0,
                    "errors": errors,
                }
            )
        return result

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ShardDB":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
