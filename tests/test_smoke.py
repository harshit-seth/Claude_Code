"""Smoke test for the command-line entry point."""

from types import SimpleNamespace

import claude_code.cli as cli
from claude_code.context import ContextManager
from claude_code.llm import LLMResponse


def test_main_streams_reply_and_exits(monkeypatch, capsys) -> None:
    prompts = iter(["Hello", "/clear", "Again", "/exit"])
    received_messages = []
    saved_sessions = []

    class FakeSession:
        def prompt(self, message: str) -> str:
            assert message == "You> "
            return next(prompts)

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "test-key"

        def stream_response(self, messages, system_prompt, tools, on_text) -> LLMResponse:
            assert system_prompt.startswith(cli.SYSTEM_PROMPT)
            assert str(cli.Path.cwd()) in system_prompt
            assert {tool["function"]["name"] for tool in tools} == {
                "read_file",
                "list_dir",
                "grep",
                "glob",
                "write_file",
                "edit_file",
                "bash",
            }
            received_messages.append(list(messages))
            on_text("Hi")
            on_text(" there")
            return LLMResponse(
                content="Hi there",
                tool_calls=[],
                finish_reason="stop",
            )

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(api_key="test-key"))
    monkeypatch.setattr(cli, "LLMClient", FakeClient)
    monkeypatch.setattr(cli, "PromptSession", FakeSession)
    monkeypatch.setattr(
        cli,
        "ContextManager",
        lambda: ContextManager(token_counter=lambda messages: len(messages) + 11),
    )
    monkeypatch.setattr(
        cli,
        "append_session_log",
        lambda transcript, project_root, started_at: saved_sessions.append(
            list(transcript)
        ),
    )

    cli.main([])

    assert received_messages == [
        [{"role": "user", "content": "Hello"}],
        [{"role": "user", "content": "Again"}],
    ]
    output = capsys.readouterr().out
    assert output.count("Hi there") == 2
    assert output.count("tokens 12/131,072") == 2
    assert saved_sessions == [
        [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "Again"},
            {"role": "assistant", "content": "Hi there"},
        ]
    ]


def test_yolo_flag_displays_warning_and_exits(monkeypatch, capsys) -> None:
    class FakeSession:
        def prompt(self, message: str) -> str:
            assert message == "You> "
            return "/exit"

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "test-key"

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(api_key="test-key"))
    monkeypatch.setattr(cli, "LLMClient", FakeClient)
    monkeypatch.setattr(cli, "PromptSession", FakeSession)
    monkeypatch.setattr(cli, "append_session_log", lambda *_args: None)

    cli.main(["--yolo"])

    assert "--yolo skips all mutating-tool approval prompts" in capsys.readouterr().out
