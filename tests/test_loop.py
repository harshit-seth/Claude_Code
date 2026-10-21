"""Tests for the core agent interaction loop."""

from typing import Any
from pathlib import Path

import pytest

from agent.tools.base import Tool, ToolRegistry
from claude_code.context import ContextManager
from claude_code.llm import LLMResponse, ToolCall
from claude_code.loop import run_agent_loop
from claude_code.permissions import PermissionManager


def _allow_all_permissions() -> PermissionManager:
    """Build a prompt-free permission manager for read-only test tools."""

    def approve(prompt: str) -> str:
        return "y" if prompt.startswith("Allow ") else "n"

    def ignore_diff(diff: str) -> None:
        assert isinstance(diff, str)

    return PermissionManager(
        project_root=Path.cwd(),
        prompt=approve,
        show_diff=ignore_diff,
        yolo=True,
    )


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
    token_usages = []
    observed_messages = []

    run_agent_loop(
        messages=messages,
        llm_client=client,
        tool_registry=registry,
        system_prompt="You are a coding assistant.",
        on_text=streamed_text.append,
        on_tool_call=lambda name, arguments: displayed_tool_calls.append(
            (name, arguments)
        ),
        permission_manager=_allow_all_permissions(),
        context_manager=ContextManager(token_counter=lambda messages: len(messages)),
        on_token_usage=token_usages.append,
        on_message=observed_messages.append,
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
    assert [message["role"] for message in observed_messages] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]


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
    token_usages = []

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
            permission_manager=_allow_all_permissions(),
            context_manager=ContextManager(
                token_counter=lambda messages: len(messages)
            ),
            on_token_usage=token_usages.append,
            max_iterations=1,
        )
    assert streamed_text == ["Working"]
    assert displayed_tools == []


def test_agent_loop_sends_denial_to_model_without_running_tool(tmp_path: Path) -> None:
    class MutatingTool(Tool):
        name = "write_file"
        description = "Write a file."
        input_schema = {"type": "object", "properties": {}}
        ran = False

        def run(self, **kwargs: Any) -> str:
            assert kwargs == {"path": "new.txt", "content": "data"}
            self.ran = True
            return "unexpected write"

    registry = ToolRegistry()
    tool = MutatingTool()
    registry.register(tool)
    responses = iter(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_write",
                        name="write_file",
                        arguments={"path": "new.txt", "content": "data"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="I will not make that change.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    model_calls = []
    shown_diffs = []

    class FakeLLMClient:
        def stream_response(self, messages, system_prompt, tools, on_text):
            assert system_prompt == "system"
            assert tools == registry.get_definitions()
            model_calls.append(list(messages))
            response = next(responses)
            if response.content:
                on_text(response.content)
            return response

    permission_manager = PermissionManager(
        project_root=tmp_path,
        prompt=lambda prompt: "n" if prompt.startswith("Allow write_file?") else "y",
        show_diff=shown_diffs.append,
    )

    streamed_text = []
    displayed_tool_calls = []
    token_usages = []
    run_agent_loop(
        messages=[{"role": "user", "content": "Write a file"}],
        llm_client=FakeLLMClient(),
        tool_registry=registry,
        system_prompt="system",
        on_text=streamed_text.append,
        on_tool_call=lambda name, arguments: displayed_tool_calls.append(
            (name, arguments)
        ),
        permission_manager=permission_manager,
        context_manager=ContextManager(token_counter=lambda messages: len(messages)),
        on_token_usage=token_usages.append,
    )

    assert tool.ran is False
    assert len(shown_diffs) == 1
    assert "+data" in shown_diffs[0]
    assert model_calls[1][-1] == {
        "role": "tool",
        "tool_call_id": "call_write",
        "name": "write_file",
        "content": "User denied tool 'write_file'.",
    }
    assert streamed_text == ["I will not make that change."]
    assert displayed_tool_calls == [("write_file", {"path": "new.txt", "content": "data"})]
