"""Find project files by glob pattern."""

from pathlib import Path, PurePath
from typing import Any

from agent.tools.base import Tool


class GlobTool(Tool):
    """Find files matching a project-relative glob pattern."""

    name = "glob"
    description = "Find files under the project root that match a glob pattern."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern relative to the project root, such as **/*.py.",
            }
        },
        "required": ["pattern"],
    }

    def __init__(self, project_root: Path) -> None:
        """Set the root directory to search."""
        self._project_root = project_root.resolve()

    def run(self, **kwargs: Any) -> str:
        """Return sorted, project-relative file paths matching the pattern."""
        pattern = kwargs["pattern"]
        parsed_pattern = PurePath(pattern)
        if parsed_pattern.is_absolute() or ".." in parsed_pattern.parts:
            raise ValueError("Glob pattern must stay within the project root.")

        matches = sorted(
            path.relative_to(self._project_root).as_posix()
            for path in self._project_root.glob(pattern)
            if path.is_file() and _is_within_root(path, self._project_root)
        )
        return "\n".join(matches) if matches else "No files found."


def _is_within_root(path: Path, root: Path) -> bool:
    """Return whether a resolved path is contained in the search root."""
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True
