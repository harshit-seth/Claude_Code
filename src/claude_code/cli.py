"""Command-line entry point."""

from pathlib import Path
from typing import Any

from anthropic.types import MessageParam
from prompt_toolkit import PromptSession
from rich.console import Console

from agent.tools import ListDirTool, ReadFileTool, ToolRegistry
from claude_code.config import load_config
from claude_code.llm import LLMClient
from claude_code.loop import run_agent_loop

SYSTEM_PROMPT = "You are a helpful coding assistant."


def main() -> None:
    """Run the interactive coding assistant."""
    config = load_config()
    client = LLMClient(config.api_key)
    session: PromptSession[str] = PromptSession()
    console = Console()
    history: list[MessageParam] = []
    tool_registry = ToolRegistry()
    project_root = Path.cwd()
    tool_registry.register(ReadFileTool(project_root))
    tool_registry.register(ListDirTool(project_root))

    while True:
        try:
            user_input = session.prompt("You> ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        command = user_input.strip()
        if command == "/exit":
            break
        if command == "/clear":
            history.clear()
            console.clear()
            continue
        if not command:
            continue

        history.append({"role": "user", "content": user_input})
        console.print("Claude> ", end="", markup=False)

        def show_tool_call(name: str, arguments: dict[str, Any]) -> None:
            argument_text = ", ".join(str(value) for value in arguments.values())
            console.print()
            console.print(f"→ {name}({argument_text})", markup=False)
            console.print("Claude> ", end="", markup=False)

        run_agent_loop(
            messages=history,
            llm_client=client,
            tool_registry=tool_registry,
            system_prompt=SYSTEM_PROMPT,
            on_text=lambda text: console.print(
                text, end="", markup=False, highlight=False
            ),
            on_tool_call=show_tool_call,
        )
        console.print()
