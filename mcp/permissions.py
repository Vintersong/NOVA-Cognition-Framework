from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class ToolPermissionContext:
    """
    Immutable permission context for NOVA MCP tools.

    Populated at server startup from environment variables:
      NOVA_DENIED_TOOLS    — comma-separated exact tool names to block
      NOVA_DENIED_PREFIXES — comma-separated prefixes; any tool whose name
                             starts with a prefix is blocked

    When both fields are empty (the DEFAULT instance) every tool is permitted.
    """

    denied_tools: frozenset[str] = field(default_factory=frozenset)
    denied_prefixes: tuple[str, ...] = ()

    DEFAULT: ClassVar[ToolPermissionContext]

    @classmethod
    def from_iterables(
        cls,
        deny_tools: list[str] | None = None,
        deny_prefixes: list[str] | None = None,
    ) -> ToolPermissionContext:
        """Construct from plain lists, normalising to lowercase and stripping whitespace."""
        return cls(
            denied_tools=frozenset(
                t.strip().lower() for t in (deny_tools or []) if t.strip()
            ),
            denied_prefixes=tuple(
                p.strip().lower() for p in (deny_prefixes or []) if p.strip()
            ),
        )

    def blocks(self, tool_name: str) -> bool:
        """Return True if *tool_name* is blocked by this context."""
        lowered = tool_name.lower()
        return lowered in self.denied_tools or any(
            lowered.startswith(prefix) for prefix in self.denied_prefixes
        )


# Sentinel: no restrictions — used when no env vars are set.
ToolPermissionContext.DEFAULT = ToolPermissionContext()
