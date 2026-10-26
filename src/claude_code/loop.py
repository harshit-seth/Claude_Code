"""Agent interaction loop."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

from agent.tools.base import ToolRegistry
from claude_code.context import ContextManager, TokenUsage
from claude_code.events import (
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    PermissionRequestEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from claude_code.llm import LLMClient, LLMResponse, ToolCall
from claude_code.permissions import PermissionHandler, PermissionManager

DEFAULT_MAX_ITERATIONS = 25


async def run_agent_events(
    messages: list[dict[str, Any]],
    llm_client: LLMClient,
    tool_registry: ToolRegistry,
    system_prompt: str,
    permission_handler: PermissionHandler,
    context_manager: ContextManager,
    cancellation_event: asyncio.Event | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncIterator[AgentEvent]:
    """Yield typed events while running one cancellable agent turn."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1.")
    cancellation_event = cancellation_event or asyncio.Event()
    cancelled = False
    tool_definitions = tool_registry.get_definitions()

    try:
        for iteration in range(max_iterations):
            if cancellation_event.is_set():
                cancelled = True
                break

            token_usage = context_manager.compact_if_needed(messages, llm_client)
            text_queue: asyncio.Queue[str] = asyncio.Queue()
            response_task = asyncio.create_task(
                _request_response(
                    llm_client,
                    messages,
                    system_prompt,
                    tool_definitions,
                    text_queue,
                    cancellation_event,
                )
            )
            cancellation_task = asyncio.create_task(cancellation_event.wait())
            try:
                while not response_task.done():
                    wait_for_text = asyncio.create_task(text_queue.get())
                    done, _ = await asyncio.wait(
                        {response_task, cancellation_task, wait_for_text},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancellation_task in done and cancellation_event.is_set():
                        wait_for_text.cancel()
                        await asyncio.gather(
                            wait_for_text, return_exceptions=True
                        )
                        response_task.cancel()
                        await asyncio.gather(response_task, return_exceptions=True)
                        cancelled = True
                        break
                    if wait_for_text in done:
                        yield TextDeltaEvent(
                            type="text_delta", text=wait_for_text.result()
                        )
                    else:
                        wait_for_text.cancel()
                        await asyncio.gather(wait_for_text, return_exceptions=True)
                if cancelled:
                    break
                response = await response_task
                while not text_queue.empty():
                    yield TextDeltaEvent(
                        type="text_delta", text=text_queue.get_nowait()
                    )
            except asyncio.CancelledError:
                response_task.cancel()
                await asyncio.gather(response_task, return_exceptions=True)
                cancelled = cancellation_event.is_set()
                if not cancelled:
                    raise
                break
            finally:
                cancellation_task.cancel()
                await asyncio.gather(cancellation_task, return_exceptions=True)

            assistant_message = _assistant_message(response)
            messages.append(assistant_message)
            if on_message is not None:
                on_message(assistant_message)

            output_tokens = _estimate_output_tokens(response)
            context_percent = (
                token_usage.count / context_manager.model_context_window * 100
            )
            yield UsageEvent(
                type="usage",
                input_tokens=token_usage.count,
                output_tokens=output_tokens,
                context_percent=context_percent,
                estimated=token_usage.estimated,
            )

            if not response.tool_calls:
                break
            if iteration == max_iterations - 1:
                yield ErrorEvent(
                    type="error",
                    message=f"Agent reached the maximum of {max_iterations} iterations.",
                )
                break

            for call in response.tool_calls:
                if cancellation_event.is_set():
                    cancelled = True
                    break
                yield ToolCallEvent(
                    type="tool_call", name=call.name, args=call.arguments
                )
                policy_error = permission_handler.blocked_reason(
                    call.name, call.arguments
                )
                if policy_error is not None:
                    result = _tool_result_message(
                        call, policy_error, context_manager
                    )
                    messages.append(result)
                    if on_message is not None:
                        on_message(result)
                    yield ToolResultEvent(
                        type="tool_result",
                        name=call.name,
                        output=result["content"],
                        is_error=True,
                    )
                    continue

                if permission_handler.requires_approval(call.name):
                    request_id = str(uuid4())
                    try:
                        diff = permission_handler.preview_diff(
                            call.name, call.arguments
                        )
                    except (OSError, ValueError, KeyError) as error:
                        result = _tool_result_message(
                            call,
                            f"Permission denied: unable to preview file change: {error}",
                            context_manager,
                        )
                        messages.append(result)
                        if on_message is not None:
                            on_message(result)
                        yield ToolResultEvent(
                            type="tool_result",
                            name=call.name,
                            output=result["content"],
                            is_error=True,
                        )
                        continue
                    permission_handler.register_request(
                        request_id, call.name, call.arguments, diff
                    )
                    permission_completed = False
                    decision_task: asyncio.Task[Any] | None = None
                    permission_cancel_task: asyncio.Task[bool] | None = None
                    try:
                        yield PermissionRequestEvent(
                            type="permission_request",
                            id=request_id,
                            tool=call.name,
                            args=call.arguments,
                            diff=diff,
                        )
                        decision_task = asyncio.create_task(
                            permission_handler.request(
                                call.name, call.arguments
                            )
                        )
                        permission_cancel_task = asyncio.create_task(
                            cancellation_event.wait()
                        )
                        done, _ = await asyncio.wait(
                            {decision_task, permission_cancel_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if (
                            permission_cancel_task in done
                            and cancellation_event.is_set()
                        ):
                            decision_task.cancel()
                            await asyncio.gather(
                                decision_task, return_exceptions=True
                            )
                            cancelled = True
                            break
                        permission_cancel_task.cancel()
                        await asyncio.gather(
                            permission_cancel_task, return_exceptions=True
                        )
                        decision = await decision_task
                        permission_completed = True
                    finally:
                        pending_tasks = [
                            task
                            for task in (
                                decision_task,
                                permission_cancel_task,
                            )
                            if task is not None and not task.done()
                        ]
                        for task in pending_tasks:
                            task.cancel()
                        if pending_tasks:
                            await asyncio.gather(
                                *pending_tasks, return_exceptions=True
                            )
                        if not permission_completed:
                            permission_handler.cancel_request(request_id)
                else:
                    decision = await permission_handler.request(
                        call.name, call.arguments
                    )

                if decision == "deny":
                    denial = (
                        permission_handler.blocked_reason(
                            call.name, call.arguments
                        )
                        or f"User denied tool '{call.name}'."
                    )
                    result = _tool_result_message(call, denial, context_manager)
                else:
                    try:
                        output = await tool_registry.execute_async(
                            call.name,
                            cancellation_event=cancellation_event,
                            **call.arguments,
                        )
                    except asyncio.CancelledError:
                        cancelled = cancellation_event.is_set()
                        if not cancelled:
                            raise
                        break
                    result = _tool_result_message(call, output, context_manager)
                messages.append(result)
                if on_message is not None:
                    on_message(result)
                is_error = result["content"].startswith(
                    (
                        "Error:",
                        "User denied",
                        "Permission denied",
                        "Blocked dangerous",
                    )
                )
                yield ToolResultEvent(
                    type="tool_result",
                    name=call.name,
                    output=result["content"],
                    is_error=is_error,
                )
            if cancelled:
                break
    except asyncio.CancelledError:
        cancelled = True
    except Exception as error:
        yield ErrorEvent(type="error", message=str(error))
    yield DoneEvent(type="done", cancelled=cancelled or cancellation_event.is_set())


async def _request_response(
    llm_client: LLMClient,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
    text_queue: asyncio.Queue[str],
    cancellation_event: asyncio.Event,
) -> LLMResponse:
    """Call an async LLM client, with a threaded adapter for existing clients."""
    async_stream = getattr(llm_client, "stream_response_async", None)
    if async_stream is not None:
        return await async_stream(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            on_text=text_queue.put_nowait,
            cancellation_event=cancellation_event,
        )
    loop = asyncio.get_running_loop()

    def on_text(text: str) -> None:
        loop.call_soon_threadsafe(text_queue.put_nowait, text)

    return await asyncio.to_thread(
        llm_client.stream_response,
        messages=messages,
        system_prompt=system_prompt,
        tools=tools,
        on_text=on_text,
    )


def _estimate_output_tokens(response: LLMResponse) -> int:
    """Estimate output tokens from response text and serialized tool calls."""
    output = response.content or ""
    if response.tool_calls:
        output += json.dumps(
            [
                {"name": call.name, "arguments": call.arguments}
                for call in response.tool_calls
            ],
            ensure_ascii=False,
        )
    return (len(output) + 3) // 4


def _tool_result_message(
    call: ToolCall, output: str, context_manager: ContextManager
) -> dict[str, Any]:
    """Serialize and bound a tool result for the conversation history."""
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": context_manager.truncate_tool_output(output),
    }


def run_agent_loop(
    messages: list[dict[str, Any]],
    llm_client: LLMClient,
    tool_registry: ToolRegistry,
    system_prompt: str,
    on_text: Callable[[str], None],
    on_tool_call: Callable[[str, dict[str, Any]], None],
    permission_manager: PermissionManager,
    context_manager: ContextManager,
    on_token_usage: Callable[[TokenUsage], None],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Compatibility adapter for the original synchronous callback API."""
    events = run_agent_events(
        messages=messages,
        llm_client=llm_client,
        tool_registry=tool_registry,
        system_prompt=system_prompt,
        permission_handler=permission_manager,
        context_manager=context_manager,
        max_iterations=max_iterations,
        on_message=on_message,
    )

    async def consume() -> None:
        async for event in events:
            if isinstance(event, TextDeltaEvent):
                on_text(event.text)
            elif isinstance(event, ToolCallEvent):
                on_tool_call(event.name, event.args)
            elif isinstance(event, UsageEvent):
                on_token_usage(
                    TokenUsage(event.input_tokens, estimated=event.estimated)
                )
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.message)

    asyncio.run(consume())



def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    """Serialize an assistant response in OpenAI-compatible message format."""
    message: dict[str, Any] = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": _serialize_arguments(call.arguments),
                },
            }
            for call in response.tool_calls
        ]
    return message


def _serialize_arguments(arguments: dict[str, Any]) -> str:
    """Serialize tool arguments for the assistant history message."""
    return json.dumps(arguments)
