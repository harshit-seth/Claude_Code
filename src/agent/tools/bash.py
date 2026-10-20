"""Run an explicitly approved shell command in the project root."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.tools.base import Tool

COMMAND_TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARACTERS = 20_000
TRUNCATION_NOTE = "\n... (command output truncated)"


class BashTool(Tool):
    """Run shell commands only after explicit approval."""

    name = "bash"
    description = (
        "Run a shell command in the project root. The user must approve each "
        "command before it runs; stdout, stderr, and exit code are returned."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."}
        },
        "required": ["command"],
    }

    def __init__(
        self,
        project_root: Path,
        approval_callback: Callable[[str], bool] | None = None,
    ) -> None:
        """Set the command working directory and optional approval callback."""
        self._project_root = project_root.resolve()
        self._approval_callback = approval_callback

    def run(self, **kwargs: Any) -> str:
        """Run an approved command with a timeout and bounded output."""
        command = kwargs["command"]
        if self._approval_callback is None:
            raise PermissionError("Shell commands require explicit user approval.")
        if not self._approval_callback(command):
            return "Command execution denied by user."

        try:
            result = subprocess.run(
                command,
                cwd=self._project_root,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                shell=True,
                check=False,
            )
            return _format_result(
                command, str(result.returncode), result.stdout, result.stderr
            )
        except subprocess.TimeoutExpired as error:
            return _format_result(
                command,
                "timeout",
                _to_text(error.stdout),
                _to_text(error.stderr),
            )


def _format_result(command: str, exit_code: str, stdout: str, stderr: str) -> str:
    """Format command execution details for the model."""
    command_text = command if len(command) <= 500 else f"{command[:497]}..."
    header = f"Command: {command_text}\nExit code: {exit_code}\n"
    prefix = f"{header}STDOUT:\n"
    separator = "\nSTDERR:\n"
    complete_output = f"{prefix}{stdout}{separator}{stderr}"
    if len(complete_output) <= MAX_OUTPUT_CHARACTERS:
        return complete_output

    available = (
        MAX_OUTPUT_CHARACTERS
        - len(prefix)
        - len(separator)
        - len(TRUNCATION_NOTE)
    )
    stdout_limit = min(len(stdout), available // 2)
    stderr_limit = min(len(stderr), available - stdout_limit)
    stdout_limit = min(len(stdout), available - stderr_limit)
    return (
        f"{prefix}{stdout[:stdout_limit]}{separator}"
        f"{stderr[:stderr_limit]}{TRUNCATION_NOTE}"
    )


def _to_text(output: str | bytes | None) -> str:
    """Normalize timeout output to text."""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output or ""

