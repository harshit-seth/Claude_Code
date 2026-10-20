"""Base classes and registry for agent tools."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Tool(ABC):
    """Abstract interface for an executable agent tool."""

    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    def run(self, **kwargs: Any) -> str:
        """Execute the tool and return its textual result."""


class ToolRegistry:
    """Register tools and expose them in Anthropic's tool format."""

    def __init__(self) -> None:
        """Initialize an empty tool registry."""
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool, rejecting duplicate names."""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get_definitions(self) -> list[dict[str, Any]]:
        """Return registered tools as Anthropic API definitions."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, **kwargs: Any) -> str:
        """Run a named tool and report any execution error as text."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'."
        try:
            return tool.run(**kwargs)
        except Exception as error:
            return f"Error running tool '{name}': {error}"


def resolve_project_path(project_root: Path, path: str) -> Path:
    """Resolve a path and reject targets outside the project root."""
    root = project_root.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Path is outside the project root: {path}") from error
    return target
