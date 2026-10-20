"""Tests for language model streaming."""

import pytest

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

    class FakeStream:
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

    class FakeMessages:
        def stream(self, model, max_tokens, system, messages) -> FakeStream:
            assert model == llm.MODEL
            assert max_tokens == 4096
            assert system == "system"
            assert messages == []
            return FakeStream()

    class FakeAnthropic:
        def __init__(self, *, api_key: str, max_retries: int) -> None:
            assert api_key == "test-key"
            assert max_retries == 0
            self.messages = FakeMessages()

    monkeypatch.setattr(llm, "APIStatusError", FakeStatusError)
    monkeypatch.setattr(llm, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(llm.time, "sleep", delays.append)

    client = llm.LLMClient("test-key")

    assert list(client.stream([], "system")) == ["Hello", " there"]
    assert attempts == 2
    assert delays == [llm.INITIAL_RETRY_DELAY_SECONDS]
