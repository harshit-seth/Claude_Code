"""Replace one exact occurrence of text in a project file."""

from pathlib import Path
from typing import Any

from agent.tools.base import Tool, resolve_project_path


class EditFileTool(Tool):
    """Apply an exact, unique text replacement to a project file."""

    name = "edit_file"
    description = (
        "Replace one exact, unique text occurrence in a project file. "
        "This minimizes unrelated changes compared with rewriting the entire file."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project-relative file path."},
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def __init__(self, project_root: Path) -> None:
        """Set the root that edits are restricted to."""
        self._project_root = project_root

    def run(self, **kwargs: Any) -> str:
        """Replace old_string only when it occurs exactly once."""
        old_string = kwargs["old_string"]
        if not old_string:
            raise ValueError("old_string must not be empty.")

        path = resolve_project_path(self._project_root, kwargs["path"])
        contents = path.read_text(encoding="utf-8")
        occurrences = contents.count(old_string)
        if occurrences == 0:
            raise ValueError("old_string was not found; file was not changed.")
        if occurrences > 1:
            raise ValueError(
                f"old_string appears {occurrences} times; expected exactly once. "
                "File was not changed."
            )

        path.write_text(
            contents.replace(old_string, kwargs["new_string"], 1),
            encoding="utf-8",
        )
        return f"Updated {kwargs['path']}."
