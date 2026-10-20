"""Conversation token counting, tool output truncation, and compaction."""

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from anthropic import Anthropic

from claude_code.config import MODEL_CONTEXT_WINDOW

TOKEN_COUNT_MODEL: Final[str] = "claude-sonnet-4-6"
TOKEN_THRESHOLD: Final[float] = 0.70
MESSAGES_TO_KEEP: Final[int] = 6
MAX_TOOL_OUTPUT_CHARACTERS: Final[int] = 20_000
TRUNCATION_MARKER: Final[str] = "[... truncated ...]"
CHARACTERS_PER_ESTIMATED_TOKEN: Final[int] = 4
SUMMARY_PROMPT: Final[str] = (
    "Summarize the earlier conversation for an assistant continuing the work. "
    "Preserve decisions, user goals, constraints, file changes, and unresolved "
    "questions. Be concise and factual."
)


@dataclass(frozen=True)
class TokenUsage:
    """Token count and whether it was estimated rather than API-counted."""

    count: int
    estimated: bool


class ConversationSummarizer(Protocol):
    """Model client interface required for conversation compaction."""

    def summarize(self, messages: Sequence[dict[str, Any]]) -> str:
        """Summarize older conversation messages."""


class ContextManager:
    """Keep conversation history within the model's context budget."""

    def __init__(
        self,
        model_context_window: int = MODEL_CONTEXT_WINDOW,
        token_counter: Callable[[Sequence[dict[str, Any]]], int] | None = None,
        anthropic_api_key: str | None = None,
        tool_output_limit: int = MAX_TOOL_OUTPUT_CHARACTERS,
    ) -> None:
        """Configure token counting, context threshold, and tool output size."""
        if model_context_window < 1:
            raise ValueError("model_context_window must be positive.")
        if tool_output_limit < len(TRUNCATION_MARKER):
            raise ValueError("tool_output_limit must fit the truncation marker.")

        self._model_context_window = model_context_window
        self._token_counter = token_counter
        self._anthropic_api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        self._tool_output_limit = tool_output_limit

    @property
    def model_context_window(self) -> int:
        """Return the configured context-window size."""
        return self._model_context_window

    def count_tokens(self, messages: Sequence[dict[str, Any]]) -> TokenUsage:
        """Count conversation tokens with Anthropic, falling back to a text estimate."""
        if self._token_counter is not None:
            try:
                return TokenUsage(self._token_counter(messages), estimated=False)
            except Exception:
                return self._estimate_tokens(messages)

        if self._anthropic_api_key:
            try:
                with Anthropic(
                    api_key=self._anthropic_api_key, max_retries=0
                ) as client:
                    response = client.messages.count_tokens(
                        model=TOKEN_COUNT_MODEL,
                        messages=[
                            {
                                "role": "user",
                                "content": json.dumps(messages, ensure_ascii=False),
                            }
                        ],
                    )
                return TokenUsage(response.input_tokens, estimated=False)
            except Exception:
                return self._estimate_tokens(messages)

        return self._estimate_tokens(messages)

    def truncate_tool_output(self, output: str) -> str:
        """Keep the head and tail of long tool output within its character limit."""
        if len(output) <= self._tool_output_limit:
            return output

        available = self._tool_output_limit - len(TRUNCATION_MARKER)
        head_length = available // 2
        tail_length = available - head_length
        return output[:head_length] + TRUNCATION_MARKER + output[-tail_length:]

    def compact_if_needed(
        self,
        messages: list[dict[str, Any]],
        llm_client: ConversationSummarizer,
    ) -> TokenUsage:
        """Summarize older history when usage exceeds 70%, preserving the last six."""
        self.truncate_existing_tool_outputs(messages)
        usage = self.count_tokens(messages)
        threshold = int(self._model_context_window * TOKEN_THRESHOLD)
        if usage.count <= threshold or len(messages) <= MESSAGES_TO_KEEP:
            return usage

        older_messages = messages[:-MESSAGES_TO_KEEP]
        recent_messages = messages[-MESSAGES_TO_KEEP:]
        summary = llm_client.summarize(older_messages)
        if not summary:
            raise ValueError("Model returned an empty conversation summary.")

        summary_message = {
            "role": "assistant",
            "content": f"Summary of earlier conversation:\n{summary}",
        }
        messages[:] = [summary_message, *recent_messages]
        return self.count_tokens(messages)

    def truncate_existing_tool_outputs(
        self, messages: list[dict[str, Any]]
    ) -> None:
        """Apply the output limit to tool results already present in history."""
        for message in messages:
            if message.get("role") == "tool" and isinstance(
                message.get("content"), str
            ):
                message["content"] = self.truncate_tool_output(message["content"])

    def _estimate_tokens(self, messages: Sequence[dict[str, Any]]) -> TokenUsage:
        serialized = json.dumps(messages, ensure_ascii=False, default=str)
        estimate = (
            len(serialized) + CHARACTERS_PER_ESTIMATED_TOKEN - 1
        ) // CHARACTERS_PER_ESTIMATED_TOKEN
        return TokenUsage(estimate, estimated=True)
