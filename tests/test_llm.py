"""Tests for Groq-compatible language model streaming."""

from types import SimpleNamespace

import pytest

import claude_code.llm as llm


def _chunk(content=None, tool_calls=None, finish_reason=None):
    """Build a minimal OpenAI-compatible streaming chunk."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ]
    )


def test_stream_response_yields_text_and_parses_tool_calls(monkeypatch) -> None:
    seen = {}

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            assert (exc_type, exc_value, traceback) == (None, None, None)
            return None

        def __iter__(self):
            yield _chunk(content="Reading ")
            yield _chunk(
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="call_123",
                        function=SimpleNamespace(
                            name="read_file", arguments='{"path":"src/'
                        ),
                    )
                ]
            )
            yield _chunk(
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id=None,
                        function=SimpleNamespace(
                            name=None, arguments='main.py"}'
                        ),
                    )
                ],
                finish_reason="tool_calls",
            )

    class FakeCompletions:
        def create(self, **kwargs):
            seen.update(kwargs)
            return FakeStream()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            seen["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    client = llm.LLMClient("test-key")
    text_parts = []

    response = client.stream_response(
        messages=[{"role": "user", "content": "Read the file"}],
        system_prompt="System prompt",
        tools=[{"type": "function"}],
        on_text=text_parts.append,
    )

    assert seen["client"] == {
        "api_key": "test-key",
        "base_url": llm.GROQ_BASE_URL,
        "max_retries": 0,
    }
    assert seen["model"] == llm.MODEL
    assert seen["messages"] == [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Read the file"},
    ]
    assert seen["tools"] == [{"type": "function"}]
    assert seen["stream"] is True
    assert text_parts == ["Reading "]
    assert response.content == "Reading "
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_123"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "src/main.py"}


@pytest.mark.parametrize("status_code", [429, 503])
def test_stream_response_retries_transient_errors(monkeypatch, status_code: int) -> None:
    attempts = 0
    delays = []

    class FakeStatusError(Exception):
        def __init__(self, error_status_code: int) -> None:
            self.status_code = error_status_code

    class FakeStream:
        def __enter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FakeStatusError(status_code)
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            assert (exc_type, exc_value, traceback) == (None, None, None)
            return None

        def __iter__(self):
            yield _chunk(content="Hello", finish_reason="stop")

    class FakeCompletions:
        def create(self, model, max_tokens, messages, tools, stream):
            assert model == llm.MODEL
            assert max_tokens == 4096
            assert messages == [
                {"role": "system", "content": "system"},
            ]
            assert tools == []
            assert stream is True
            return FakeStream()

    class FakeOpenAI:
        def __init__(self, api_key, base_url, max_retries) -> None:
            assert api_key == "test-key"
            assert base_url == llm.GROQ_BASE_URL
            assert max_retries == 0
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(llm, "APIStatusError", FakeStatusError)
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm.time, "sleep", delays.append)

    client = llm.LLMClient("test-key")
    text_parts = []

    response = client.stream_response([], "system", [], text_parts.append)

    assert response.content == "Hello"
    assert text_parts == ["Hello"]
    assert attempts == 2
    assert delays == [llm.INITIAL_RETRY_DELAY_SECONDS]
