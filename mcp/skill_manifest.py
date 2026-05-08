"""
skill_manifest.py — Skill verification and capability metadata.

Parses @@verification and @@capabilities from SKILL.md content strings.
These two fields gate HITL behavior and capability checks in the Forgemaster
hook dispatch path, per the NOVA Skill Verification Layer design (Metere 2026).

Verification levels (fixed enum, not a score):
  unverified — no attestation; all irreversible calls fire HITL
  declared   — operator has read and attested to bounded side-effects
  tested     — declaration + passed adversarial verification run
  formal     — machine-checkable proof (aspirational, reserved)

Capability wildcard '*' is accepted as @@capabilities value during the Phase 2
migration window so existing skills do not break before explicit declarations
are written.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

logger = logging.getLogger(__name__)

CAPABILITY_WILDCARD = "*"


class VerificationLevel(str, Enum):
    UNVERIFIED = "unverified"
    DECLARED   = "declared"
    TESTED     = "tested"
    FORMAL     = "formal"

    @classmethod
    def _missing_(cls, value: object) -> "VerificationLevel":
        logger.warning(
            "skill_manifest: unknown @@verification value %r — defaulting to unverified",
            value,
        )
        return cls.UNVERIFIED


@dataclass(frozen=True)
class SkillManifest:
    """
    Parsed metadata extracted from a SKILL.md file's @@ header block.

    Immutable after construction — verification level cannot be upgraded
    during a session.  Any attempt to modify a loaded skill's content is
    itself an irreversible operation that must walk the HITL gate.
    """

    skill_id: str
    verification: VerificationLevel
    capabilities: frozenset[str]
    raw_capabilities: str = ""

    # Sentinel: operator-direct calls not tied to a loaded skill.
    # Treated as tested + wildcard so direct MCP use never fires HITL.
    OPERATOR_DIRECT: ClassVar["SkillManifest"]

    def has_capability(self, cap: str) -> bool:
        """Return True if *cap* is declared or the wildcard is set."""
        return CAPABILITY_WILDCARD in self.capabilities or cap in self.capabilities

    def allows_undeclared(self) -> bool:
        """Return True if this manifest uses the wildcard capability grant."""
        return CAPABILITY_WILDCARD in self.capabilities


# Operator-direct: trusted context for calls not tied to a loaded skill.
# This is the default active_skill for all direct MCP calls from Claude Code.
SkillManifest.OPERATOR_DIRECT = SkillManifest(
    skill_id="operator:direct",
    verification=VerificationLevel.TESTED,
    capabilities=frozenset([CAPABILITY_WILDCARD]),
)


def parse_skill_manifest(skill_id: str, content: str) -> SkillManifest:
    """
    Extract @@verification and @@capabilities from a SKILL.md content string.

    Defaults when fields are absent:
      @@verification  → unverified  (logged as warning)
      @@capabilities  → empty set   (all capability calls require declaration)

    The @@capabilities value '*' is treated as a wildcard that passes all
    capability checks; this is the Phase 2 migration default.

    The parser accepts @@ headers anywhere in the content — not only at the
    top — so they can be inserted without breaking existing markdown structure.
    """
    verification = VerificationLevel.UNVERIFIED
    capabilities: frozenset[str] = frozenset()
    raw_caps = ""
    found_verification = False

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@@"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped[2:].partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "verification":
            found_verification = True
            verification = VerificationLevel(value.lower())
        elif key == "capabilities":
            raw_caps = value
            capabilities = frozenset(
                c.strip() for c in value.split(",") if c.strip()
            )

    if not found_verification:
        logger.warning(
            "skill_manifest: @@verification absent in skill %r — defaulting to unverified",
            skill_id,
        )

    return SkillManifest(
        skill_id=skill_id,
        verification=verification,
        capabilities=capabilities,
        raw_capabilities=raw_caps,
    )
