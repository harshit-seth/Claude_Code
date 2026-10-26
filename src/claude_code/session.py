"""Per-conversation state for a coding-agent session."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.tools.base import ToolRegistry
from claude_code.context import ContextManager
from claude_code.permissions import PermissionHandler


@dataclass
class AgentSession:
    """Own the project boundary and mutable state for one agent conversation."""

    project_root: Path
    tool_registry: ToolRegistry
    permission_handler: PermissionHandler
    context_manager: ContextManager = field(default_factory=ContextManager)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalize the project root once when the session is created."""
        self.project_root = self.project_root.resolve()
