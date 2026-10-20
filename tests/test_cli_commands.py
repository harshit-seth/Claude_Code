"""Tests for CLI initialization and planning commands."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import claude_code.cli as cli
from claude_code.context import ContextManager


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


def _tool_names(tool_registry) -> set[str]:
    return {
        definition["function"]["name"]
        for definition in tool_registry.get_definitions()
    }


def test_plan_uses_read_only_tools_until_approved(monkeypatch, tmp_path: Path) -> None:
    _configure_cli(monkeypatch, tmp_path, ["/plan Add a feature", "approve", "/exit"])
    calls: list[tuple[set[str], str, list[dict[str, Any]]]] = []

    def fake_run_agent_loop(
        messages: list[dict[str, Any]],
        tool_registry,
        system_prompt: str,
        **_kwargs: Any,
    ) -> None:
        assert isinstance(_kwargs["llm_client"], FakeClient)
        assert callable(_kwargs["on_text"])
        calls.append((_tool_names(tool_registry), system_prompt, list(messages)))
        if "Plan mode" in system_prompt:
            messages.append({"role": "assistant", "content": "1. Implement it."})

    monkeypatch.setattr(cli, "run_agent_loop", fake_run_agent_loop)

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

    def fake_run_agent_loop(
        messages: list[dict[str, Any]],
        tool_registry,
        **_kwargs: Any,
    ) -> None:
        assert isinstance(_kwargs["llm_client"], FakeClient)
        assert callable(_kwargs["on_text"])
        calls.append((_tool_names(tool_registry), list(messages)))
        messages.append({"role": "assistant", "content": "1. Implement it."})

    monkeypatch.setattr(cli, "run_agent_loop", fake_run_agent_loop)

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

    def fake_run_agent_loop(
        messages: list[dict[str, Any]],
        tool_registry,
        system_prompt: str,
        **_kwargs: Any,
    ) -> None:
        assert isinstance(_kwargs["llm_client"], FakeClient)
        assert callable(_kwargs["on_text"])
        names = _tool_names(tool_registry)
        calls.append((names, system_prompt, list(messages)))
        if messages[-1]["content"] == cli.INIT_INSTRUCTIONS:
            (tmp_path / "AGENT.md").write_text("Use pytest.", encoding="utf-8")
        messages.append({"role": "assistant", "content": "Done."})

    monkeypatch.setattr(cli, "run_agent_loop", fake_run_agent_loop)

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
