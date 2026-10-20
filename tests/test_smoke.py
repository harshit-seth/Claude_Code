"""Smoke test for the command-line entry point."""

from types import SimpleNamespace

import claude_code.cli as cli


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

        def stream(self, messages, system_prompt: str):
            assert system_prompt == cli.SYSTEM_PROMPT
            received_messages.append(list(messages))
            yield "Hi"
            yield " there"

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(api_key="test-key"))
    monkeypatch.setattr(cli, "LLMClient", FakeClient)
    monkeypatch.setattr(cli, "PromptSession", FakeSession)

    cli.main()

    assert received_messages == [
        [{"role": "user", "content": "Hello"}],
        [{"role": "user", "content": "Again"}],
    ]
    assert capsys.readouterr().out.count("Claude> Hi there") == 2
