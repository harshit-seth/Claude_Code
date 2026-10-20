"""Language model integration."""

import time
from collections.abc import Callable, Sequence
from typing import Any

from anthropic import APIStatusError, Anthropic
from anthropic.types import Message, MessageParam

from claude_code.config import MODEL

MAX_RETRIES = 3
INITIAL_RETRY_DELAY_SECONDS = 1.0


class LLMClient:
    """Stream text responses from the Anthropic Messages API."""

    def __init__(self, api_key: str) -> None:
        """Create an Anthropic client using the configured API key."""
        self._client = Anthropic(api_key=api_key, max_retries=0)

    def stream_response(
        self,
        messages: Sequence[MessageParam],
        system_prompt: str,
        tools: Sequence[dict[str, Any]],
        on_text: Callable[[str], None],
    ) -> Message:
        """Stream response text and return the final message."""
        for attempt in range(MAX_RETRIES + 1):
            yielded_text = False
            try:
                with self._client.messages.stream(
                    model=MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                ) as response:
                    for text in response.text_stream:
                        yielded_text = True
                        on_text(text)
                    return response.get_final_message()
            except APIStatusError as error:
                retryable = error.status_code == 429 or error.status_code == 529
                if not retryable or yielded_text or attempt == MAX_RETRIES:
                    raise
                time.sleep(INITIAL_RETRY_DELAY_SECONDS * (2**attempt))

        raise RuntimeError("Anthropic request exhausted retries without a response.")
