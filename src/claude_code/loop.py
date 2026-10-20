"""Agent interaction loop."""

from collections.abc import Callable
from typing import Any

from anthropic.types import MessageParam, ToolUseBlock

from agent.tools.base import ToolRegistry
from claude_code.llm import LLMClient

DEFAULT_MAX_ITERATIONS = 25


def run_agent_loop(
    messages: list[MessageParam],
    llm_client: LLMClient,
    tool_registry: ToolRegistry,
    system_prompt: str,
    on_text: Callable[[str], None],
    on_tool_call: Callable[[str, dict[str, Any]], None],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run model and tool turns, appending each exchange to conversation history."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1.")

    tool_definitions = tool_registry.get_definitions()
    for iteration in range(max_iterations):
        response = llm_client.stream_response(
            messages=messages,
            system_prompt=system_prompt,
            tools=tool_definitions,
            on_text=on_text,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return
        if iteration == max_iterations - 1:
            raise RuntimeError(
                f"Agent reached the maximum of {max_iterations} iterations."
            )

        tool_results = [
            _execute_tool(block, tool_registry, on_tool_call)
            for block in response.content
            if isinstance(block, ToolUseBlock)
        ]
        if not tool_results:
            raise RuntimeError("Model requested tool use without a tool call.")
        messages.append({"role": "user", "content": tool_results})


def _execute_tool(
    block: ToolUseBlock,
    tool_registry: ToolRegistry,
    on_tool_call: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Notify the UI and return a result block for a model tool call."""
    arguments = dict(block.input)
    on_tool_call(block.name, arguments)
    result = tool_registry.execute(block.name, **arguments)
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": result,
    }
