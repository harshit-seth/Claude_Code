"""List project directory contents."""

from pathlib import Path
from typing import Any

from agent.tools.base import Tool, resolve_project_path


class ListDirTool(Tool):
    """List entries within the project root."""

    name = "list_dir"
    description = "List files and directories under a project directory."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to the project root; defaults to the root.",
            }
        },
    }

    def __init__(self, project_root: Path) -> None:
        """Set the root that directory listings are restricted to."""
        self._project_root = project_root

    def run(self, **kwargs: Any) -> str:
        """Return sorted names from a project directory."""
        relative_path = kwargs.get("path", ".")
        directory = resolve_project_path(self._project_root, relative_path)
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {relative_path}")

        entries = sorted(
            directory.iterdir(),
            key=lambda entry: (not entry.is_dir(), entry.name.casefold()),
        )
        return "\n".join(
            f"{entry.name}{'/' if entry.is_dir() else ''}" for entry in entries
        )
