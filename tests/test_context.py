"""Tests for conversation context management."""

from collections.abc import Sequence
import json
from types import SimpleNamespace
from typing import Any

import claude_code.context as context
from claude_code.context import (
    MAX_TOOL_OUTPUT_CHARACTERS,
    MESSAGES_TO_KEEP,
    TRUNCATION_MARKER,
    ContextManager,
)


def test_compacts_old_messages_with_fake_llm_and_keeps_last_six() -> None:
    messages = [
        {"role": "user", "content": f"old-{index}"} for index in range(4)
    ] + [{"role": "assistant", "content": f"recent-{index}"} for index in range(6)]
    recent_messages = [message.copy() for message in messages[-MESSAGES_TO_KEEP:]]
    summary_inputs: list[list[dict[str, Any]]] = []

    class FakeLLM:
        def summarize(self, older_messages: Sequence[dict[str, Any]]) -> str:
            summary_inputs.append(list(older_messages))
            return "Earlier discussion covered project goals."

    manager = ContextManager(
        model_context_window=10,
        token_counter=lambda current_messages: (
            100 if len(current_messages) == 10 else len(current_messages)
        ),
    )

    usage = manager.compact_if_needed(messages, FakeLLM())

    assert summary_inputs == [[{"role": "user", "content": f"old-{i}"} for i in range(4)]]
    assert messages == [
        {
            "role": "assistant",
            "content": "Summary of earlier conversation:\n"
            "Earlier discussion covered project goals.",
        },
        *recent_messages,
    ]
    assert usage.count == 7
    assert usage.estimated is False


def test_does_not_summarize_when_below_threshold() -> None:
    messages = [{"role": "user", "content": f"message-{index}"} for index in range(8)]

    class FakeLLM:
        def summarize(self, messages: Sequence[dict[str, Any]]) -> str:
            assert len(messages) == 4
            raise AssertionError("No summary should be requested.")

    def count_below_threshold(messages: Sequence[dict[str, Any]]) -> int:
        return 69 if len(messages) == 8 else 70

    manager = ContextManager(
        model_context_window=100,
        token_counter=count_below_threshold,
    )

    usage = manager.compact_if_needed(messages, FakeLLM())

    assert len(messages) == 8
    assert usage.count == 69


def test_falls_back_to_character_estimate_when_token_api_fails() -> None:
    def fail_token_count(messages: Sequence[dict[str, Any]]) -> int:
        assert messages[0]["content"] == "12345678"
        raise RuntimeError("token endpoint unavailable")

    messages = [{"role": "user", "content": "12345678"}]
    manager = ContextManager(token_counter=fail_token_count)

    usage = manager.count_tokens(messages)

    assert usage.estimated is True
    serialized = json.dumps(messages, ensure_ascii=False, default=str)
    assert usage.count == (len(serialized) + 3) // 4


def test_counts_tokens_with_anthropic_api_when_key_is_available(monkeypatch) -> None:
    calls = []

    class FakeMessages:
        def count_tokens(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(input_tokens=123)

    class FakeAnthropic:
        def __init__(self, *, api_key: str, max_retries: int) -> None:
            assert api_key == "test-anthropic-key"
            assert max_retries == 0
            self.messages = FakeMessages()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            assert (exc_type, exc_value, traceback) == (None, None, None)

    monkeypatch.setattr(context, "Anthropic", FakeAnthropic)
    manager = ContextManager(anthropic_api_key="test-anthropic-key")

    usage = manager.count_tokens([{"role": "user", "content": "hello"}])

    assert usage.count == 123
    assert usage.estimated is False
    assert calls[0]["model"] == context.TOKEN_COUNT_MODEL


def test_falls_back_when_anthropic_token_api_raises(monkeypatch) -> None:
    class FakeAnthropic:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {
                "api_key": "test-anthropic-key",
                "max_retries": 0,
            }
            raise RuntimeError("token service unavailable")

    monkeypatch.setattr(context, "Anthropic", FakeAnthropic)
    manager = ContextManager(anthropic_api_key="test-anthropic-key")

    usage = manager.count_tokens([{"role": "user", "content": "12345678"}])

    assert usage.count > 0
    assert usage.estimated is True


def test_truncates_long_tool_output_preserving_head_and_tail() -> None:
    manager = ContextManager(tool_output_limit=100)
    output = "H" * 80 + "middle" + "T" * 80

    truncated = manager.truncate_tool_output(output)

    assert len(truncated) == 100
    assert truncated.startswith("H" * 40)
    assert TRUNCATION_MARKER in truncated
    assert truncated.endswith("T" * 41)


def test_truncates_existing_tool_messages_before_counting() -> None:
    messages = [{"role": "tool", "content": "x" * (MAX_TOOL_OUTPUT_CHARACTERS + 1)}]
    manager = ContextManager(
        token_counter=lambda current: len(current[0]["content"])
    )

    class FakeLLM:
        def summarize(self, messages: Sequence[dict[str, Any]]) -> str:
            return "unused summary"

    usage = manager.compact_if_needed(messages, llm_client=FakeLLM())

    assert len(messages[0]["content"]) == MAX_TOOL_OUTPUT_CHARACTERS
    assert TRUNCATION_MARKER in messages[0]["content"]
    assert usage.count == MAX_TOOL_OUTPUT_CHARACTERS
