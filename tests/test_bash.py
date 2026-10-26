"""Tests for the explicitly approved shell command tool."""

import asyncio
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import agent.tools.bash as bash_module
from agent.tools import BashTool


def test_bash_runs_approved_command_and_formats_result(
    monkeypatch, tmp_path: Path
) -> None:
    command_details = {}

    def fake_run(command, **kwargs):
        command_details["command"] = command
        command_details.update(kwargs)
        return SimpleNamespace(returncode=3, stdout="result\n", stderr="warning\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = BashTool(tmp_path)

    result = tool.run(command="pytest")

    assert command_details["command"] == "pytest"
    assert command_details["cwd"] == tmp_path.resolve()
    assert command_details["timeout"] == 30
    assert command_details["capture_output"] is True
    assert "Exit code: 3" in result
    assert "STDOUT:\nresult\n" in result
    assert "STDERR:\nwarning\n" in result


def test_bash_reports_timeout_and_truncates_output(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(command, **kwargs):
        assert command == "slow"
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=30,
            output=b"x" * 25_000,
            stderr=b"timeout stderr",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = BashTool(tmp_path)

    result = tool.run(command="slow")

    assert len(result) == 20_000
    assert "Exit code: timeout" in result
    assert "STDOUT:\n" in result
    assert "STDERR:\ntimeout stderr" in result
    assert result.endswith("... (command output truncated)")


def test_bash_async_run_can_be_cancelled(monkeypatch, tmp_path: Path) -> None:
    tool = BashTool(tmp_path)
    cancellation_event = asyncio.Event()
    process_terminated = threading.Event()

    class FakeProcess:
        pid = 123

        def __init__(self, command, **kwargs) -> None:
            assert command == "long-running"
            assert kwargs["cwd"] == tmp_path.resolve()
            self.returncode = None

        def communicate(self):
            process_terminated.wait(timeout=3)
            return "", ""

        def poll(self):
            return self.returncode

        def send_signal(self, _signal) -> None:
            self.returncode = 0
            process_terminated.set()

        def kill(self) -> None:
            self.returncode = -1
            process_terminated.set()

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    if bash_module.os.name != "nt":
        monkeypatch.setattr(
            bash_module.os,
            "killpg",
            lambda _pid, _signal: process_terminated.set(),
        )

    async def run_and_cancel() -> float:
        started = time.monotonic()
        task = asyncio.create_task(
            tool.run_async(cancellation_event, command="long-running")
        )
        await asyncio.sleep(0.2)
        cancellation_event.set()
        try:
            await asyncio.wait_for(task, timeout=3)
        except asyncio.CancelledError:
            return time.monotonic() - started
        raise AssertionError("The shell task did not cancel.")

    assert asyncio.run(run_and_cancel()) < 3
