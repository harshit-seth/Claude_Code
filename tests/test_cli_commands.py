"""Tests for CLI initialization and planning commands."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import claude_code.cli as cli
from claude_code.context import ContextManager
from claude_code.events import DoneEvent, TextDeltaEvent, UsageEvent


class FakeSession:
    def __init__(self, prompts: list[str]) -> None:
        self._prompts = iter(prompts)

    def prompt(self, _message: str) -> str:
        assert isinstance(_message, str)
        return next(self._prompts)


class FakeClient:
    def __init__(self, _api_key: str) -> None:
        assert _api_key == "test"


def _configure_cli(monkeypatch, tmp_path: Path, prompts: list[str]) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(api_key="test"))
    monkeypatch.setattr(cli, "LLMClient", FakeClient)
    monkeypatch.setattr(cli, "PromptSession", lambda: FakeSession(prompts))
    def project_cwd(cls: type[Path]) -> Path:
        assert cls is Path
        return tmp_path

    monkeypatch.setattr(cli.Path, "cwd", classmethod(project_cwd))
    monkeypatch.setattr(
        cli,
        "ContextManager",
        lambda: ContextManager(token_counter=lambda messages: len(messages)),
    )
    monkeypatch.setattr(cli, "append_session_log", lambda *_args: None)


def test_prompt_runs_one_noninteractive_turn_and_reports_usage(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _configure_cli(monkeypatch, tmp_path, [])
    saved_sessions = []

    def save_session(transcript, project_root, started_at):
        saved_sessions.append((list(transcript), project_root, started_at))

    async def fake_run_agent_events(messages, **kwargs: Any):
        assistant_message = {"role": "assistant", "content": "Done"}
        messages.append(assistant_message)
        kwargs["on_message"](assistant_message)
        yield TextDeltaEvent(type="text_delta", text="Done")
        yield UsageEvent(
            type="usage",
            input_tokens=12,
            output_tokens=5,
            context_percent=0.01,
            estimated=True,
        )
        yield DoneEvent(type="done", cancelled=False)

    monkeypatch.setattr(cli, "append_session_log", save_session)
    monkeypatch.setattr(cli, "run_agent_events", fake_run_agent_events)

    cli.main(["--prompt", "Fix the bug", "--yolo"])

    output = capsys.readouterr().out
    assert "EVAL_TOKENS_USED=17 EVAL_TOKENS_ESTIMATED=true" in output
    assert len(saved_sessions) == 1
    assert saved_sessions[0][0] == [
        {"role": "user", "content": "Fix the bug"},
        {"role": "assistant", "content": "Done"},
    ]
    assert saved_sessions[0][1] == tmp_path


def _tool_names(tool_registry) -> set[str]:
    return {
        definition["function"]["name"]
        for definition in tool_registry.get_definitions()
    }


def test_plan_uses_read_only_tools_until_approved(monkeypatch, tmp_path: Path) -> None:
    _configure_cli(monkeypatch, tmp_path, ["/plan Add a feature", "approve", "/exit"])
    calls: list[tuple[set[str], str, list[dict[str, Any]]]] = []

    async def fake_run_agent_events(
        messages: list[dict[str, Any]],
        system_prompt: str,
        tool_registry,
        **_kwargs: Any,
    ):
        assert isinstance(_kwargs["llm_client"], FakeClient)
        calls.append((_tool_names(tool_registry), system_prompt, list(messages)))
        if "Plan mode" in system_prompt:
            messages.append({"role": "assistant", "content": "1. Implement it."})
        yield DoneEvent(type="done", cancelled=False)

    monkeypatch.setattr(cli, "run_agent_events", fake_run_agent_events)

    cli.main([])

    read_only_names = {"read_file", "list_dir", "grep", "glob"}
    assert calls[0][0] == read_only_names
    assert "step-by-step implementation plan" in calls[0][1]
    assert len(calls) == 2
    assert calls[1][0] == read_only_names | {"write_file", "edit_file", "bash"}
    assert calls[1][2][-1]["content"].startswith("I approve the plan")


def test_plan_revision_replans_read_only_and_can_be_cancelled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_cli(
        monkeypatch,
        tmp_path,
        ["/plan Add a feature", "revise", "Include tests", "cancel", "/exit"],
    )
    calls: list[tuple[set[str], list[dict[str, Any]]]] = []

    async def fake_run_agent_events(
        messages: list[dict[str, Any]],
        tool_registry,
        **_kwargs: Any,
    ):
        assert isinstance(_kwargs["llm_client"], FakeClient)
        calls.append((_tool_names(tool_registry), list(messages)))
        messages.append({"role": "assistant", "content": "1. Implement it."})
        yield DoneEvent(type="done", cancelled=False)

    monkeypatch.setattr(cli, "run_agent_events", fake_run_agent_events)

    cli.main([])

    assert len(calls) == 2
    assert all(call[0] == {"read_file", "list_dir", "grep", "glob"} for call in calls)
    assert calls[1][1][-1] == {"role": "user", "content": "Include tests"}


def test_init_limits_tools_and_loads_generated_instructions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_cli(monkeypatch, tmp_path, ["/init", "hello", "/exit"])
    calls: list[tuple[set[str], str, list[dict[str, Any]]]] = []

    async def fake_run_agent_events(
        messages: list[dict[str, Any]],
        tool_registry,
        system_prompt: str,
        **_kwargs: Any,
    ):
        assert isinstance(_kwargs["llm_client"], FakeClient)
        names = _tool_names(tool_registry)
        calls.append((names, system_prompt, list(messages)))
        if messages[-1]["content"] == cli.INIT_INSTRUCTIONS:
            (tmp_path / "AGENT.md").write_text("Use pytest.", encoding="utf-8")
        messages.append({"role": "assistant", "content": "Done."})
        yield DoneEvent(type="done", cancelled=False)

    monkeypatch.setattr(cli, "run_agent_events", fake_run_agent_events)

    cli.main([])

    assert calls[0][0] == {"read_file", "list_dir", "grep", "glob", "write_file"}
    assert "AGENT.md" in calls[0][2][0]["content"]
    assert calls[1][0] == {
        "read_file",
        "list_dir",
        "grep",
        "glob",
        "write_file",
        "edit_file",
        "bash",
    }
    assert "Project instructions\nUse pytest." in calls[1][1]
