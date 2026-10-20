"""Agent interaction loop."""

import json
from collections.abc import Callable
from typing import Any

from agent.tools.base import ToolRegistry
from claude_code.context import ContextManager, TokenUsage
from claude_code.llm import LLMClient, LLMResponse, ToolCall
from claude_code.permissions import PermissionManager

DEFAULT_MAX_ITERATIONS = 25


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
) -> None:
    """Run model and tool turns, appending each exchange to conversation history."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1.")

    tool_definitions = tool_registry.get_definitions()
    for iteration in range(max_iterations):
        usage = context_manager.compact_if_needed(messages, llm_client)
        on_token_usage(usage)
        response = llm_client.stream_response(
            messages=messages,
            system_prompt=system_prompt,
            tools=tool_definitions,
            on_text=on_text,
        )
        messages.append(_assistant_message(response))

        if not response.tool_calls:
            return
        if iteration == max_iterations - 1:
            raise RuntimeError(
                f"Agent reached the maximum of {max_iterations} iterations."
            )

        tool_results = [
            _execute_tool(
                call,
                tool_registry,
                on_tool_call,
                permission_manager,
                context_manager,
            )
            for call in response.tool_calls
        ]
        messages.extend(tool_results)


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


def _execute_tool(
    call: ToolCall,
    tool_registry: ToolRegistry,
    on_tool_call: Callable[[str, dict[str, Any]], None],
    permission_manager: PermissionManager,
    context_manager: ContextManager,
) -> dict[str, Any]:
    """Notify the UI and return a result block for a model tool call."""
    arguments = call.arguments
    on_tool_call(call.name, arguments)
    denial_message = permission_manager.authorize(call.name, arguments)
    result = (
        denial_message
        if denial_message is not None
        else tool_registry.execute(call.name, **arguments)
    )
    result = context_manager.truncate_tool_output(result)
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": result,
    }


def _serialize_arguments(arguments: dict[str, Any]) -> str:
    """Serialize tool arguments for the assistant history message."""
    return json.dumps(arguments)
