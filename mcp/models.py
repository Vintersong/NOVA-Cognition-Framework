from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageSummary:
    """
    Immutable per-session token usage tracker.

    Token counts are word-count based estimates (same approach as
    claw-code-2-electric-bugaloo/src/models.py).  Each call to
    ``add_turn`` returns a *new* instance with updated totals so the
    module-level ``_session_usage`` value can be reassigned without
    mutating shared state.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens for this session."""
        return self.input_tokens + self.output_tokens

    def add_turn(self, prompt: str, output: str) -> UsageSummary:
        """Return a new ``UsageSummary`` with this turn's word counts added."""
        return UsageSummary(
            input_tokens=self.input_tokens + len(prompt.split()),
            output_tokens=self.output_tokens + len(output.split()),
        )


@dataclass(frozen=True)
class ShardRecord:
    """Typed Python representation of a NOVA shard's key metadata fields."""

    shard_id: str
    guiding_question: str
    intent: str
    theme: str
    usage_count: int
    last_used: str


@dataclass(frozen=True)
class PermissionDenial:
    """Record of a tool call that was blocked by the permission context."""

    tool_name: str
    reason: str
