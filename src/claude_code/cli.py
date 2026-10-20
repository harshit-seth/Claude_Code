"""Command-line entry point."""

from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from rich.console import Console

from agent.tools import (
    BashTool,
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    ToolRegistry,
    WriteFileTool,
)
from claude_code.config import load_config
from claude_code.llm import LLMClient
from claude_code.loop import run_agent_loop

SYSTEM_PROMPT = "You are a helpful coding assistant."


def build_system_prompt(project_root: Path) -> str:
    """Describe available tools and their project-root boundary to the model."""
    return (
        f"{SYSTEM_PROMPT} The project root is {project_root.resolve()}. "
        "Available tools: read_file, list_dir, grep, glob, write_file, "
        "edit_file, and bash. File tools operate only under the project root. "
        "Use edit_file for a single exact change. The bash tool requires "
        "explicit user approval for every command."
    )


def main() -> None:
    """Run the interactive coding assistant."""
    config = load_config()
    client = LLMClient(config.api_key)
    session: PromptSession[str] = PromptSession()
    console = Console()
    history: list[dict[str, Any]] = []
    tool_registry = ToolRegistry()
    project_root = Path.cwd()
    tool_registry.register(ReadFileTool(project_root))
    tool_registry.register(ListDirTool(project_root))
    tool_registry.register(GrepTool(project_root))
    tool_registry.register(GlobTool(project_root))
    tool_registry.register(WriteFileTool(project_root))
    tool_registry.register(EditFileTool(project_root))

    def approve_command(command: str) -> bool:
        """Ask the user before running model-requested shell commands."""
        console.print(f"Requested shell command: {command}", markup=False)
        return session.prompt("Run this command? [y/N] ").strip().lower() in {
            "y",
            "yes",
        }

    tool_registry.register(BashTool(project_root, approve_command))

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
            system_prompt=build_system_prompt(project_root),
            on_text=lambda text: console.print(
                text, end="", markup=False, highlight=False
            ),
            on_tool_call=show_tool_call,
        )
        console.print()
