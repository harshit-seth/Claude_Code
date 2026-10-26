"""Run an explicitly approved shell command in the project root."""

import asyncio
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from agent.tools.base import Tool

COMMAND_TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARACTERS = 20_000
TRUNCATION_NOTE = "\n... (command output truncated)"


class BashTool(Tool):
    """Run shell commands after the agent loop authorizes the tool call."""

    name = "bash"
    description = (
        "Run a shell command in the project root; the agent permission system "
        "must authorize it first. Returns stdout, stderr, and exit code."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."}
        },
        "required": ["command"],
    }

    def __init__(self, project_root: Path) -> None:
        """Set the command working directory."""
        self._project_root = project_root.resolve()

    def run(self, **kwargs: Any) -> str:
        """Run a command with a timeout and bounded output."""
        command = kwargs["command"]
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

    async def run_async(
        self,
        cancellation_event: asyncio.Event,
        **kwargs: Any,
    ) -> str:
        """Run a shell command while allowing the process to be terminated."""
        command = kwargs["command"]
        if cancellation_event.is_set():
            raise asyncio.CancelledError
        process_options: dict[str, Any] = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=self._project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **process_options,
        )
        communicate_task = asyncio.create_task(asyncio.to_thread(process.communicate))
        cancel_task = asyncio.create_task(cancellation_event.wait())
        try:
            done, _ = await asyncio.wait(
                {communicate_task, cancel_task},
                timeout=COMMAND_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and cancellation_event.is_set():
                _signal_process_tree(process, terminate=True)
                try:
                    await asyncio.wait_for(
                        asyncio.shield(communicate_task), timeout=2
                    )
                except TimeoutError:
                    _signal_process_tree(process, terminate=False)
                    communicate_task.cancel()
                    await asyncio.gather(
                        communicate_task, return_exceptions=True
                    )
                raise asyncio.CancelledError
            if communicate_task not in done:
                _signal_process_tree(process, terminate=False)
                stdout, stderr = await communicate_task
                return _format_result(
                    command,
                    "timeout",
                    _to_text(stdout),
                    _to_text(stderr),
                )
            stdout, stderr = communicate_task.result()
            return _format_result(
                command,
                str(process.returncode),
                stdout,
                stderr,
            )
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)


def _signal_process_tree(
    process: subprocess.Popen[str], terminate: bool
) -> None:
    """Signal a shell process and its children during cancellation or timeout."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        if terminate:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                return
            except (OSError, ValueError):
                pass
        process.kill()
        return

    import signal as posix_signal

    process_signal = posix_signal.SIGTERM if terminate else posix_signal.SIGKILL
    try:
        os.killpg(process.pid, process_signal)
    except ProcessLookupError:
        pass


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
