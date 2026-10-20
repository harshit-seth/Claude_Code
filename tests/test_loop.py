"""Tests for the core agent interaction loop."""

from typing import Any

import pytest

from agent.tools.base import Tool, ToolRegistry
from claude_code.llm import LLMResponse, ToolCall
from claude_code.loop import run_agent_loop


class ReadTool(Tool):
    """Fake read tool for agent loop tests."""

    name = "read_file"
    description = "Read a file."
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}

    def run(self, **kwargs: Any) -> str:
        return f"contents of {kwargs['path']}"


def test_agent_loop_executes_tool_then_returns_final_answer() -> None:
    tool_response = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id="call_123",
                name="read_file",
                arguments={"path": "src/main.py"},
            ),
            ToolCall(
                id="call_456",
                name="read_file",
                arguments={"path": "src/cli.py"},
            ),
        ],
        finish_reason="tool_calls",
    )
    final_response = LLMResponse(
        content="The files are ready.",
        tool_calls=[],
        finish_reason="stop",
    )

    class FakeLLMClient:
        def __init__(self) -> None:
            self.responses = iter([tool_response, final_response])
            self.calls = []

        def stream_response(
            self,
            messages,
            system_prompt: str,
            tools,
            on_text,
        ) -> LLMResponse:
            self.calls.append((list(messages), system_prompt, tools))
            response = next(self.responses)
            if response.content:
                on_text(response.content)
            return response

    registry = ToolRegistry()
    registry.register(ReadTool())
    client = FakeLLMClient()
    messages = [{"role": "user", "content": "Read src/main.py"}]
    streamed_text = []
    displayed_tool_calls = []

    run_agent_loop(
        messages=messages,
        llm_client=client,
        tool_registry=registry,
        system_prompt="You are a coding assistant.",
        on_text=streamed_text.append,
        on_tool_call=lambda name, arguments: displayed_tool_calls.append(
            (name, arguments)
        ),
    )

    assert len(client.calls) == 2
    assert client.calls[0][2] == registry.get_definitions()
    assert client.calls[1][0][-3] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "src/main.py"}',
                },
            },
            {
                "id": "call_456",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "src/cli.py"}',
                },
            },
        ],
    }
    assert client.calls[1][0][-2:] == [
        {
        "role": "tool",
        "tool_call_id": "call_123",
        "name": "read_file",
        "content": "contents of src/main.py",
        },
        {
            "role": "tool",
            "tool_call_id": "call_456",
            "name": "read_file",
            "content": "contents of src/cli.py",
        },
    ]
    assert displayed_tool_calls == [
        ("read_file", {"path": "src/main.py"}),
        ("read_file", {"path": "src/cli.py"}),
    ]
    assert streamed_text == ["The files are ready."]
    assert messages[-1] == {"role": "assistant", "content": "The files are ready."}


def test_agent_loop_enforces_iteration_limit() -> None:
    tool_response = LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="call_123", name="read_file", arguments={})],
        finish_reason="tool_calls",
    )

    class FakeLLMClient:
        def stream_response(
            self, messages, system_prompt, tools, on_text
        ) -> LLMResponse:
            assert messages == []
            assert system_prompt == "system"
            assert tools == registry.get_definitions()
            on_text("Working")
            return tool_response

    registry = ToolRegistry()
    registry.register(ReadTool())
    streamed_text = []
    displayed_tools = []

    with pytest.raises(RuntimeError, match="maximum of 1 iterations"):
        run_agent_loop(
            messages=[],
            llm_client=FakeLLMClient(),
            tool_registry=registry,
            system_prompt="system",
            on_text=streamed_text.append,
            on_tool_call=lambda name, arguments: displayed_tools.append(
                (name, arguments)
            ),
            max_iterations=1,
        )
    assert streamed_text == ["Working"]
    assert displayed_tools == []
