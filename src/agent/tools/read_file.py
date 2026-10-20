"""Read project files with line numbers."""

from pathlib import Path
from typing import Any

from agent.tools.base import Tool, resolve_project_path

MAX_OUTPUT_CHARACTERS = 20_000
TRUNCATION_NOTE = "\n... (output truncated at 20000 characters)"


class ReadFileTool(Tool):
    """Read a UTF-8 file contained in the project root."""

    name = "read_file"
    description = "Read a project file and return its contents with line numbers."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file relative to the project root.",
            }
        },
        "required": ["path"],
    }

    def __init__(self, project_root: Path) -> None:
        """Set the root that file reads are restricted to."""
        self._project_root = project_root

    def run(self, **kwargs: Any) -> str:
        """Read and line-number a project file."""
        path = resolve_project_path(self._project_root, kwargs["path"])
        if not path.is_file():
            raise FileNotFoundError(f"Not a file: {kwargs['path']}")

        contents = path.read_text(encoding="utf-8")
        numbered_lines = [
            f"{line_number:>4} | {line}"
            for line_number, line in enumerate(contents.splitlines(), start=1)
        ]
        output = "\n".join(numbered_lines)
        if len(output) <= MAX_OUTPUT_CHARACTERS:
            return output

        available_characters = MAX_OUTPUT_CHARACTERS - len(TRUNCATION_NOTE)
        return output[:available_characters] + TRUNCATION_NOTE
