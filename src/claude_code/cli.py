"""Command-line entry point."""

import argparse
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.syntax import Syntax

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
from claude_code.context import ContextManager, TokenUsage
from claude_code.llm import LLMClient
from claude_code.loop import run_agent_loop
from claude_code.memory import load_project_instructions
from claude_code.mcp_client import MCPClient
from claude_code.permissions import PermissionManager

SYSTEM_PROMPT = "You are a helpful coding assistant."
READ_ONLY_TOOL_NAMES = ("read_file", "list_dir", "grep", "glob")
PLAN_INSTRUCTIONS = (
    "You are in plan mode. Explore the repository with the available read-only "
    "tools, then provide a clear, numbered, step-by-step implementation plan. "
    "Do not make changes or execute mutating actions."
)
INIT_INSTRUCTIONS = (
    "Explore the repository using the available read-only tools. Then create or "
    "update AGENT.md at the project root with concise project-specific guidance: "
    "architecture, setup and test commands, coding conventions, and relevant "
    "constraints. Do not include secrets or modify any other files. Use "
    "write_file to create the file after exploring."
)


def build_system_prompt(
    project_root: Path,
    project_instructions: str | None = None,
) -> str:
    """Describe available tools and their project-root boundary to the model."""
    prompt = (
        f"{SYSTEM_PROMPT} The project root is {project_root.resolve()}. "
        "Available tools: read_file, list_dir, grep, glob, write_file, "
        "edit_file, and bash. File tools operate only under the project root. "
        "Use edit_file for a single exact change. Mutating tools require user "
        "approval unless allowed for this session or --yolo is enabled. "
        "Dangerous shell command patterns are always blocked."
    )
    if project_instructions:
        prompt += f"\n\nProject instructions\n{project_instructions}"
    return prompt


def main(argv: list[str] | None = None) -> None:
    """Run the interactive coding assistant."""
    parser = argparse.ArgumentParser(prog="claude-code")
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Skip mutating-tool approval prompts (dangerous shell patterns remain blocked).",
    )
    arguments = parser.parse_args(argv)

    config = load_config()
    client = LLMClient(config.api_key)
    session: PromptSession[str] = PromptSession()
    console = Console()
    history: list[dict[str, Any]] = []
    context_manager = ContextManager()
    tool_registry = ToolRegistry()
    project_root = Path.cwd()
    project_instructions = load_project_instructions(project_root)
    if arguments.yolo:
        console.print(
            "[bold red]WARNING: --yolo skips all mutating-tool approval prompts.[/bold red]"
        )

    tool_registry.register(ReadFileTool(project_root))
    tool_registry.register(ListDirTool(project_root))
    tool_registry.register(GrepTool(project_root))
    tool_registry.register(GlobTool(project_root))
    tool_registry.register(WriteFileTool(project_root))
    tool_registry.register(EditFileTool(project_root))

    tool_registry.register(BashTool(project_root))
    mcp_client = MCPClient(project_root)
    for failure in mcp_client.start(tool_registry):
        console.print(f"[yellow]Warning: {failure}[/yellow]")

    def show_diff(diff: str) -> None:
        """Render a colored unified diff before requesting permission."""
        console.print(Syntax(diff, "diff", theme="monokai", word_wrap=True))

    permission_manager = PermissionManager(
        project_root=project_root,
        prompt=lambda message: session.prompt(message),
        show_diff=show_diff,
        yolo=arguments.yolo,
    )
    read_only_tool_registry = tool_registry.filtered(READ_ONLY_TOOL_NAMES)
    init_tool_registry = tool_registry.filtered(
        (*READ_ONLY_TOOL_NAMES, "write_file")
    )

    def run_turn(
        messages: list[dict[str, Any]],
        active_registry: ToolRegistry,
        system_prompt: str,
    ) -> None:
        """Run one model/tool interaction and show token usage."""
        console.print("Claude> ", end="", markup=False)

        def show_tool_call(name: str, arguments: dict[str, Any]) -> None:
            """Render the requested tool call."""
            argument_text = ", ".join(str(value) for value in arguments.values())
            console.print()
            console.print(f"→ {name}({argument_text})", markup=False)
            console.print("Claude> ", end="", markup=False)

        def show_token_usage(usage: TokenUsage) -> None:
            """Display the current token usage and whether it is estimated."""
            estimate_marker = "~" if usage.estimated else ""
            console.print(
                f"[dim]tokens {estimate_marker}{usage.count:,}/"
                f"{context_manager.model_context_window:,}[/dim]"
            )

        run_agent_loop(
            messages=messages,
            llm_client=client,
            tool_registry=active_registry,
            system_prompt=system_prompt,
            on_text=lambda text: console.print(
                text, end="", markup=False, highlight=False
            ),
            on_tool_call=show_tool_call,
            permission_manager=permission_manager,
            context_manager=context_manager,
            on_token_usage=show_token_usage,
        )
        console.print()

    try:
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
            if command == "/init":
                history.append({"role": "user", "content": INIT_INSTRUCTIONS})
                run_turn(
                    history,
                    init_tool_registry,
                    build_system_prompt(project_root, project_instructions),
                )
                project_instructions = load_project_instructions(project_root)
                continue
            if command == "/plan" or command.startswith("/plan "):
                task = command[len("/plan") :].strip()
                if not task:
                    try:
                        task = session.prompt("Plan task> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        console.print()
                        continue
                if not task:
                    continue

                plan_messages = [dict(message) for message in history]
                plan_messages.append({"role": "user", "content": task})
                plan_complete = False
                while True:
                    run_turn(
                        plan_messages,
                        read_only_tool_registry,
                        build_system_prompt(project_root, project_instructions)
                        + f"\n\n{PLAN_INSTRUCTIONS}",
                    )
                    while True:
                        try:
                            decision = session.prompt(
                                "Plan decision [a] approve, [r] revise, [c] cancel: "
                            ).strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            console.print()
                            decision = "c"

                        if decision in {"a", "approve"}:
                            history[:] = plan_messages
                            history.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "I approve the plan above. Execute it now, "
                                        "asking for permission before mutating tools."
                                    ),
                                }
                            )
                            run_turn(
                                history,
                                tool_registry,
                                build_system_prompt(
                                    project_root, project_instructions
                                ),
                            )
                            plan_complete = True
                            break
                        if decision in {"r", "revise"}:
                            try:
                                revision = session.prompt("Plan revision> ").strip()
                            except (EOFError, KeyboardInterrupt):
                                console.print()
                                revision = ""
                            if revision:
                                plan_messages.append(
                                    {"role": "user", "content": revision}
                                )
                                break
                            console.print("A revision cannot be empty.")
                            continue
                        if decision in {"c", "cancel"}:
                            console.print("Plan cancelled.")
                            plan_complete = True
                            break
                        console.print("Choose approve, revise, or cancel.")
                    if plan_complete:
                        break
                continue
            if not command:
                continue

            history.append({"role": "user", "content": user_input})
            run_turn(
                history,
                tool_registry,
                build_system_prompt(project_root, project_instructions),
            )
    finally:
        mcp_client.close()
