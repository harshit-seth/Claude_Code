"""Tool permission classification and interactive authorization."""

import difflib
import re
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from agent.tools.base import resolve_project_path

READ_ONLY_TOOLS = frozenset({"read_file", "list_dir", "grep", "glob"})
DANGEROUS_BASH_PATTERNS = (
    re.compile(
        r"\brm\b(?=[^;\n]*\s-[^\s]*r)(?=[^;\n]*\s-[^\s]*f)"
        r"[^;\n]*\s/(?:\s|$|\*)"
    ),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bcurl\b[^|]*\|\s*(?:ba)?sh\b"),
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*}\s*;\s*:"),
)


class ToolPermission(str, Enum):
    """Permission class assigned to an agent tool."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"


class PermissionManager:
    """Authorize tool execution, preview file changes, and block dangerous shell commands."""

    def __init__(
        self,
        project_root: Path,
        prompt: Callable[[str], str],
        show_diff: Callable[[str], None],
        yolo: bool = False,
    ) -> None:
        """Configure interactive permission callbacks and optional prompt bypass."""
        self._project_root = project_root.resolve()
        self._prompt = prompt
        self._show_diff = show_diff
        self._yolo = yolo
        self._always_allow: set[str] = set()

    @staticmethod
    def classify_tool(tool_name: str) -> ToolPermission:
        """Classify known read-only tools; treat all others as mutating."""
        if tool_name in READ_ONLY_TOOLS:
            return ToolPermission.READ_ONLY
        return ToolPermission.MUTATING

    @staticmethod
    def blocked_bash_reason(command: str) -> str | None:
        """Return the reason a dangerous shell command is refused."""
        if any(pattern.search(command) for pattern in DANGEROUS_BASH_PATTERNS):
            return "Blocked dangerous bash command; it will not be executed."
        return None

    def authorize(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Return a denial message, or None when the tool call is authorized."""
        if tool_name == "bash":
            blocked_reason = self.blocked_bash_reason(str(arguments.get("command", "")))
            if blocked_reason is not None:
                return blocked_reason

        if self.classify_tool(tool_name) is ToolPermission.READ_ONLY:
            return None
        if self._yolo or tool_name in self._always_allow:
            return None

        if tool_name in {"edit_file", "write_file"}:
            try:
                diff = self._preview_file_change(tool_name, arguments)
            except (OSError, ValueError, KeyError) as error:
                return f"Permission denied: unable to preview file change: {error}"
            self._show_diff(diff)

        choice = self._prompt(
            f"Allow {tool_name}? [y] yes once, [a] always allow this tool "
            "in this session, [n] no: "
        ).strip().lower()
        if choice == "y":
            return None
        if choice == "a":
            self._always_allow.add(tool_name)
            return None
        return f"User denied tool '{tool_name}'."

    def _preview_file_change(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """Create the exact unified diff for a proposed write or unique edit."""
        relative_path = arguments["path"]
        path = resolve_project_path(self._project_root, relative_path)
        old_contents = path.read_text(encoding="utf-8") if path.exists() else ""

        if tool_name == "write_file":
            new_contents = arguments["content"]
        else:
            old_string = arguments["old_string"]
            occurrences = old_contents.count(old_string)
            if not old_string or occurrences != 1:
                raise ValueError(
                    "edit_file requires old_string to occur exactly once "
                    f"(found {occurrences})."
                )
            new_contents = old_contents.replace(
                old_string, arguments["new_string"], 1
            )

        diff = difflib.unified_diff(
            old_contents.splitlines(keepends=True),
            new_contents.splitlines(keepends=True),
            fromfile=f"a/{relative_path}" if path.exists() else "/dev/null",
            tofile=f"b/{relative_path}",
        )
        return "".join(diff) or "(No file changes.)"
