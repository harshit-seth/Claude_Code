"""Language model integration."""

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openai import APIStatusError, AsyncOpenAI, OpenAI

from claude_code.config import MODEL
from claude_code.context import SUMMARY_PROMPT

MAX_RETRIES = 3
INITIAL_RETRY_DELAY_SECONDS = 1.0
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class ToolCall:
    """A parsed model-requested tool call."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """The assistant content and tool calls returned by the model."""

    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str | None


class LLMClient:
    """Stream text and tool calls from Groq's OpenAI-compatible API."""

    def __init__(self, api_key: str) -> None:
        """Create a Groq API client using the OpenAI SDK."""
        self._api_key = api_key
        self._client = OpenAI(
            api_key=api_key,
            base_url=GROQ_BASE_URL,
            max_retries=0,
        )
        self._async_client: AsyncOpenAI | None = None

    async def aclose(self) -> None:
        """Close the synchronous and asynchronous provider clients."""
        if self._async_client is not None:
            await self._async_client.close()
        self._client.close()

    def generate_title(self, prompt: str) -> str:
        """Generate a short chat title from its first user message."""
        response = self._client.chat.completions.create(
            model=MODEL,
            max_tokens=40,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Create a concise chat title of at most six words. "
                        "Return only the title."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        title = response.choices[0].message.content
        if not title:
            raise ValueError("Model returned an empty chat title.")
        return title.strip().strip('"')

    async def stream_response_async(
        self,
        messages: Sequence[dict[str, Any]],
        system_prompt: str,
        tools: Sequence[dict[str, Any]],
        on_text: Callable[[str], None],
        cancellation_event: asyncio.Event,
    ) -> LLMResponse:
        """Stream an async response and close the provider stream on cancellation."""
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=GROQ_BASE_URL,
                max_retries=0,
            )

        for attempt in range(MAX_RETRIES + 1):
            received_event = False
            content_parts: list[str] = []
            tool_calls: dict[int, dict[str, str]] = {}
            finish_reason: str | None = None
            try:
                stream = await self._async_client.chat.completions.create(
                    model=MODEL,
                    max_tokens=4096,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                    tools=tools,
                    stream=True,
                )
                async with stream:
                    iterator = stream.__aiter__()
                    while True:
                        if cancellation_event.is_set():
                            raise asyncio.CancelledError
                        next_chunk = asyncio.create_task(iterator.__anext__())
                        cancelled = asyncio.create_task(cancellation_event.wait())
                        done, _ = await asyncio.wait(
                            {next_chunk, cancelled},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if cancelled in done and cancellation_event.is_set():
                            next_chunk.cancel()
                            await asyncio.gather(next_chunk, return_exceptions=True)
                            raise asyncio.CancelledError
                        cancelled.cancel()
                        await asyncio.gather(cancelled, return_exceptions=True)
                        try:
                            chunk = next_chunk.result()
                        except StopAsyncIteration:
                            break

                        received_event = True
                        for choice in chunk.choices:
                            if choice.finish_reason is not None:
                                finish_reason = choice.finish_reason
                            if choice.delta.content:
                                content_parts.append(choice.delta.content)
                                on_text(choice.delta.content)
                            for call in choice.delta.tool_calls or []:
                                call_data = tool_calls.setdefault(
                                    call.index,
                                    {"id": "", "name": "", "arguments": ""},
                                )
                                if call.id:
                                    call_data["id"] += call.id
                                if call.function and call.function.name:
                                    call_data["name"] += call.function.name
                                if call.function and call.function.arguments:
                                    call_data["arguments"] += call.function.arguments

                if cancellation_event.is_set():
                    raise asyncio.CancelledError
                parsed_tool_calls = [
                    ToolCall(
                        id=call["id"],
                        name=call["name"],
                        arguments=json.loads(call["arguments"] or "{}"),
                    )
                    for _, call in sorted(tool_calls.items())
                ]
                return LLMResponse(
                    content="".join(content_parts) or None,
                    tool_calls=parsed_tool_calls,
                    finish_reason=finish_reason,
                )
            except APIStatusError as error:
                retryable = error.status_code in {429, 500, 502, 503, 529}
                if not retryable or received_event or attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(INITIAL_RETRY_DELAY_SECONDS * (2**attempt))

        raise RuntimeError("Groq request exhausted retries without a response.")

    def stream_response(
        self,
        messages: Sequence[dict[str, Any]],
        system_prompt: str,
        tools: Sequence[dict[str, Any]],
        on_text: Callable[[str], None],
    ) -> LLMResponse:
        """Stream response text, parse tool calls, and retry transient errors."""
        for attempt in range(MAX_RETRIES + 1):
            received_event = False
            content_parts: list[str] = []
            tool_calls: dict[int, dict[str, str]] = {}
            finish_reason: str | None = None
            try:
                stream = self._client.chat.completions.create(
                    model=MODEL,
                    max_tokens=4096,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                    tools=tools,
                    stream=True,
                )
                with stream:
                    for chunk in stream:
                        received_event = True
                        for choice in chunk.choices:
                            if choice.finish_reason is not None:
                                finish_reason = choice.finish_reason
                            if choice.delta.content:
                                content_parts.append(choice.delta.content)
                                on_text(choice.delta.content)
                            for call in choice.delta.tool_calls or []:
                                call_data = tool_calls.setdefault(
                                    call.index,
                                    {"id": "", "name": "", "arguments": ""},
                                )
                                if call.id:
                                    call_data["id"] += call.id
                                if call.function and call.function.name:
                                    call_data["name"] += call.function.name
                                if call.function and call.function.arguments:
                                    call_data["arguments"] += call.function.arguments

                parsed_tool_calls = [
                    ToolCall(
                        id=call["id"],
                        name=call["name"],
                        arguments=json.loads(call["arguments"] or "{}"),
                    )
                    for _, call in sorted(tool_calls.items())
                ]
                return LLMResponse(
                    content="".join(content_parts) or None,
                    tool_calls=parsed_tool_calls,
                    finish_reason=finish_reason,
                )
            except APIStatusError as error:
                retryable = error.status_code in {429, 500, 502, 503, 529}
                if not retryable or received_event or attempt == MAX_RETRIES:
                    raise
                time.sleep(INITIAL_RETRY_DELAY_SECONDS * (2**attempt))

        raise RuntimeError("Groq request exhausted retries without a response.")

    def summarize(self, messages: Sequence[dict[str, Any]]) -> str:
        """Summarize older conversation messages using the configured model."""
        response = self._client.chat.completions.create(
            model=MODEL,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(messages, ensure_ascii=False),
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Model returned an empty conversation summary.")
        return content
