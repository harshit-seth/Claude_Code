"""Create or overwrite a project file."""

from pathlib import Path
from typing import Any

from agent.tools.base import Tool, resolve_project_path


class WriteFileTool(Tool):
    """Write UTF-8 text to a file contained in the project root."""

    name = "write_file"
    description = "Create or overwrite a UTF-8 text file under the project root."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project-relative file path."},
            "content": {"type": "string", "description": "Complete file contents."},
        },
        "required": ["path", "content"],
    }

    def __init__(self, project_root: Path) -> None:
        """Set the root that writes are restricted to."""
        self._project_root = project_root

    def run(self, **kwargs: Any) -> str:
        """Create parent directories and write the requested file contents."""
        path = resolve_project_path(self._project_root, kwargs["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(kwargs["content"], encoding="utf-8")
        return f"Wrote {kwargs['path']}."
