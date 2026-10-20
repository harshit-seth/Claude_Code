"""Tests for the explicitly approved shell command tool."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

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
