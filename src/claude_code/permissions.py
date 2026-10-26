"""Tool permission classification and interactive authorization."""

import asyncio
import difflib
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Literal

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


PermissionDecision = Literal["allow_once", "allow_always", "deny"]


class PermissionHandler(ABC):
    """Async boundary for approving a tool call in any application surface."""

    def __init__(self) -> None:
        self._always_allow: set[str] = set()

    @abstractmethod
    async def request(
        self, tool: str, args: dict[str, Any]
    ) -> PermissionDecision:
        """Request authorization for one tool call."""

    def requires_approval(self, tool: str) -> bool:
        """Return whether the tool needs a user decision in this handler."""
        return (
            PermissionManager.classify_tool(tool) is ToolPermission.MUTATING
            and tool not in self._always_allow
        )

    def blocked_reason(self, tool: str, args: dict[str, Any]) -> str | None:
        """Return a policy rejection that no user decision can override."""
        if tool == "bash":
            return PermissionManager.blocked_bash_reason(
                str(args.get("command", ""))
            )
        return None

    def preview_diff(self, tool: str, args: dict[str, Any]) -> str | None:
        """Return a preview for file mutations when the handler can provide one."""
        return None

    def register_request(
        self,
        request_id: str,
        tool: str,
        args: dict[str, Any],
        diff: str | None,
    ) -> None:
        """Prepare a request before its event is exposed to an external consumer."""

    def cancel_request(self, request_id: str) -> None:
        """Discard state for a permission request whose turn was cancelled."""


class TerminalPermissionHandler(PermissionHandler):
    """Authorize tool execution, preview file changes, and block dangerous shell commands."""

    def __init__(
        self,
        project_root: Path,
        prompt: Callable[[str], str],
        show_diff: Callable[[str], None],
        yolo: bool = False,
    ) -> None:
        """Configure interactive permission callbacks and optional prompt bypass."""
        super().__init__()
        self._project_root = project_root.resolve()
        self._prompt = prompt
        self._show_diff = show_diff
        self._yolo = yolo

    @staticmethod
    def classify_tool(tool_name: str) -> ToolPermission:
        """Classify known read-only tools; treat all others as mutating."""
        if "__" in tool_name:
            return ToolPermission.MUTATING
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

    def requires_approval(self, tool: str) -> bool:
        """Return whether this terminal policy will prompt for the tool."""
        return (
            super().requires_approval(tool)
            and not self._yolo
            and self.blocked_reason(tool, {}) is None
        )

    def preview_diff(self, tool: str, args: dict[str, Any]) -> str | None:
        """Build a file-change diff for a permission request event."""
        if tool not in {"edit_file", "write_file"}:
            return None
        return self._preview_file_change(tool, args)

    async def request(
        self, tool: str, args: dict[str, Any]
    ) -> PermissionDecision:
        """Use the existing terminal prompt policy through the async interface."""
        return (
            "allow_once"
            if self.authorize(tool, args) is None
            else "deny"
        )

    def _preview_file_change(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """Create the exact unified diff for a proposed write or unique edit."""
        return _preview_file_change(self._project_root, tool_name, arguments)


PermissionManager = TerminalPermissionHandler


class WebPermissionHandler(PermissionHandler):
    """Await permission decisions submitted by an external web/API consumer."""

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self._project_root = project_root.resolve()
        self._pending: dict[str, asyncio.Future[PermissionDecision]] = {}
        self._active_request_id: str | None = None

    @property
    def project_root(self) -> Path:
        """Return the project root used for file-change previews."""
        return self._project_root

    def register_request(
        self,
        request_id: str,
        tool: str,
        args: dict[str, Any],
        diff: str | None,
    ) -> None:
        """Create a decision future before yielding its permission event."""
        del tool, args, diff
        if request_id in self._pending:
            raise ValueError(f"Permission request already exists: {request_id}")
        self._pending[request_id] = asyncio.get_running_loop().create_future()
        self._active_request_id = request_id

    @property
    def pending_request_ids(self) -> tuple[str, ...]:
        """Return request IDs awaiting decisions from external clients."""
        return tuple(
            request_id
            for request_id, future in self._pending.items()
            if not future.done()
        )

    async def request(
        self, tool: str, args: dict[str, Any]
    ) -> PermissionDecision:
        """Await a decision submitted for the currently yielded request."""
        if self.blocked_reason(tool, args) is not None:
            return "deny"
        if not self.requires_approval(tool):
            return "allow_once"
        request_id = self._active_request_id
        if request_id is None or request_id not in self._pending:
            raise RuntimeError("No registered web permission request.")
        try:
            decision = await self._pending[request_id]
        finally:
            self._pending.pop(request_id, None)
            self._active_request_id = None
        if decision == "allow_always":
            self._always_allow.add(tool)
        return decision

    def preview_diff(self, tool: str, args: dict[str, Any]) -> str | None:
        """Return the proposed unified diff for file mutation requests."""
        if tool not in {"edit_file", "write_file"}:
            return None
        return _preview_file_change(self._project_root, tool, args)

    def submit_decision(
        self, request_id: str, decision: PermissionDecision
    ) -> None:
        """Resolve a pending request from an external application."""
        if decision not in {"allow_once", "allow_always", "deny"}:
            raise ValueError(f"Unknown permission decision: {decision}")
        future = self._pending.get(request_id)
        if future is None or future.done():
            raise ValueError(f"No pending permission request: {request_id}")
        loop = future.get_loop()
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            future.set_result(decision)
        else:
            loop.call_soon_threadsafe(
                _resolve_permission_future, future, decision
            )

    def cancel_request(self, request_id: str) -> None:
        """Cancel and remove a request abandoned by its agent turn."""
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.cancel()
        if self._active_request_id == request_id:
            self._active_request_id = None


def _preview_file_change(
    project_root: Path,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Create the exact unified diff for a proposed write or unique edit."""
    relative_path = arguments["path"]
    path = resolve_project_path(project_root, relative_path)
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
        new_contents = old_contents.replace(old_string, arguments["new_string"], 1)

    diff = difflib.unified_diff(
        old_contents.splitlines(keepends=True),
        new_contents.splitlines(keepends=True),
        fromfile=f"a/{relative_path}" if path.exists() else "/dev/null",
        tofile=f"b/{relative_path}",
    )
    return "".join(diff) or "(No file changes.)"


def _resolve_permission_future(
    future: asyncio.Future[PermissionDecision],
    decision: PermissionDecision,
) -> None:
    """Resolve a permission future safely on its owning event loop."""
    if not future.done():
        future.set_result(decision)
