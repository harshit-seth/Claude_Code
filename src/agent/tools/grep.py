"""Search project files with a regular expression."""

import re
from pathlib import Path
from typing import Any

from agent.tools.base import Tool

MAX_MATCHES = 100
IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__"}


class GrepTool(Tool):
    """Search UTF-8 project files and return matching lines."""

    name = "grep"
    description = "Search project files with a regular expression; return file, line, and text."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for.",
            }
        },
        "required": ["pattern"],
    }

    def __init__(self, project_root: Path) -> None:
        """Set the root directory to search."""
        self._project_root = project_root.resolve()

    def run(self, **kwargs: Any) -> str:
        """Return matching file:line:text records, capped at 100 results."""
        expression = re.compile(kwargs["pattern"])
        matches: list[str] = []
        truncated = False

        for path in sorted(self._project_root.rglob("*")):
            if IGNORED_DIRECTORIES.intersection(path.relative_to(self._project_root).parts):
                continue
            if not path.is_file() or not _is_within_root(path, self._project_root):
                continue

            relative_path = path.relative_to(self._project_root).as_posix()
            with path.open(encoding="utf-8", errors="replace") as source:
                for line_number, line in enumerate(source, start=1):
                    if expression.search(line):
                        if len(matches) == MAX_MATCHES:
                            truncated = True
                            break
                        matches.append(
                            f"{relative_path}:{line_number}:{line.rstrip()}"
                        )
            if truncated:
                break

        if truncated:
            matches.append(f"... results truncated at {MAX_MATCHES} matches.")
        return "\n".join(matches) if matches else "No matches found."


def _is_within_root(path: Path, root: Path) -> bool:
    """Return whether a resolved path is contained in the search root."""
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True
