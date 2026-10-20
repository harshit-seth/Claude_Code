"""Tests for language model streaming."""

import pytest
from anthropic.types import Message

import claude_code.llm as llm


@pytest.mark.parametrize("status_code", [429, 529])
def test_stream_retries_rate_limit_and_overloaded_errors(
    monkeypatch, status_code: int
) -> None:
    attempts = 0
    delays = []

    class FakeStatusError(Exception):
        def __init__(self, error_status_code: int) -> None:
            self.status_code = error_status_code

    class FakeResponse:
        @property
        def text_stream(self):
            return iter(("Hello", " there"))

        def __enter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FakeStatusError(status_code)
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            assert (exc_type, exc_value, traceback) == (None, None, None)
            return None

        def get_final_message(self) -> Message:
            return Message.model_construct(
                id="message-id",
                content=[],
                model=llm.MODEL,
                role="assistant",
                stop_reason="end_turn",
                stop_sequence=None,
                type="message",
                usage=None,
            )

    class FakeAnthropic:
        def __init__(self, *, api_key: str, max_retries: int) -> None:
            assert api_key == "test-key"
            assert max_retries == 0
            self.messages = FakeMessages()

    class FakeMessages:
        def stream(self, model, max_tokens, system, messages, tools) -> FakeResponse:
            assert model == llm.MODEL
            assert max_tokens == 4096
            assert system == "system"
            assert messages == []
            assert tools == []
            return FakeResponse()

    monkeypatch.setattr(llm, "APIStatusError", FakeStatusError)
    monkeypatch.setattr(llm, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(llm.time, "sleep", delays.append)

    client = llm.LLMClient("test-key")

    text_parts = []
    response = client.stream_response([], "system", [], text_parts.append)

    assert text_parts == ["Hello", " there"]
    assert response.stop_reason == "end_turn"
    assert attempts == 2
    assert delays == [llm.INITIAL_RETRY_DELAY_SECONDS]
