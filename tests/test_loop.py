"""Tests for the core agent interaction loop."""

from typing import Any

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock

from agent.tools.base import Tool, ToolRegistry
from claude_code.loop import run_agent_loop


class ReadTool(Tool):
    """Fake read tool for agent loop tests."""

    name = "read_file"
    description = "Read a file."
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}

    def run(self, **kwargs: Any) -> str:
        return f"contents of {kwargs['path']}"


def test_agent_loop_executes_tool_then_returns_final_answer() -> None:
    tool_call = ToolUseBlock.model_construct(
        id="toolu_123",
        input={"path": "src/main.py"},
        name="read_file",
        type="tool_use",
    )
    second_tool_call = ToolUseBlock.model_construct(
        id="toolu_456",
        input={"path": "src/cli.py"},
        name="read_file",
        type="tool_use",
    )
    tool_response = Message.model_construct(
        id="message-1",
        content=[tool_call, second_tool_call],
        model="test-model",
        role="assistant",
        stop_reason="tool_use",
        stop_sequence=None,
        type="message",
        usage=None,
    )
    final_response = Message.model_construct(
        id="message-2",
        content=[TextBlock.model_construct(text="The file is ready.", type="text")],
        model="test-model",
        role="assistant",
        stop_reason="end_turn",
        stop_sequence=None,
        type="message",
        usage=None,
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
        ) -> Message:
            self.calls.append((list(messages), system_prompt, tools))
            response = next(self.responses)
            if response.stop_reason == "end_turn":
                on_text("The file is ready.")
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
    assert client.calls[1][0][-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_123",
                "content": "contents of src/main.py",
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_456",
                "content": "contents of src/cli.py",
            },
        ],
    }
    assert displayed_tool_calls == [
        ("read_file", {"path": "src/main.py"}),
        ("read_file", {"path": "src/cli.py"}),
    ]
    assert streamed_text == ["The file is ready."]
    assert messages[-1]["content"] == final_response.content


def test_agent_loop_enforces_iteration_limit() -> None:
    tool_response = Message.model_construct(
        id="message-1",
        content=[
            ToolUseBlock.model_construct(
                id="toolu_123",
                input={},
                name="read_file",
                type="tool_use",
            )
        ],
        model="test-model",
        role="assistant",
        stop_reason="tool_use",
        stop_sequence=None,
        type="message",
        usage=None,
    )

    class FakeLLMClient:
        def stream_response(
            self, messages, system_prompt, tools, on_text
        ) -> Message:
            assert messages == []
            assert system_prompt == "system"
            assert tools == registry.get_definitions()
            on_text("Thinking")
            return tool_response

    registry = ToolRegistry()
    registry.register(ReadTool())
    streamed_text = []
    tool_calls = []

    with pytest.raises(RuntimeError, match="maximum of 1 iterations"):
        run_agent_loop(
            messages=[],
            llm_client=FakeLLMClient(),
            tool_registry=registry,
            system_prompt="system",
            on_text=streamed_text.append,
            on_tool_call=lambda name, arguments: tool_calls.append(
                (name, arguments)
            ),
            max_iterations=1,
        )
    assert streamed_text == ["Thinking"]
    assert tool_calls == []
