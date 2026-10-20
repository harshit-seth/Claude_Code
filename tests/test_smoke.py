"""Smoke test for the command-line entry point."""

from types import SimpleNamespace

import claude_code.cli as cli
from claude_code.llm import LLMResponse


def test_main_streams_reply_and_exits(monkeypatch, capsys) -> None:
    prompts = iter(["Hello", "/clear", "Again", "/exit"])
    received_messages = []

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

    cli.main()

    assert received_messages == [
        [{"role": "user", "content": "Hello"}],
        [{"role": "user", "content": "Again"}],
    ]
    assert capsys.readouterr().out.count("Claude> Hi there") == 2
