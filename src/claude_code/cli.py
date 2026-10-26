"""Command-line entry point."""

import argparse
import asyncio
from datetime import datetime, timezone
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
from claude_code.context import ContextManager
from claude_code.events import (
    DoneEvent,
    ErrorEvent,
    PermissionRequestEvent,
    TextDeltaEvent,
    ToolCallEvent,
    UsageEvent,
)
from claude_code.llm import LLMClient
from claude_code.loop import run_agent_events
from claude_code.memory import load_project_instructions
from claude_code.mcp_client import MCPClient
from claude_code.permissions import TerminalPermissionHandler
from claude_code.session import AgentSession
from claude_code.session_log import append_session_log

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
    parser.add_argument(
        "--prompt",
        help="Run one prompt non-interactively, then exit.",
    )
    arguments = parser.parse_args(argv)
    if arguments.prompt is not None and not arguments.prompt.strip():
        parser.error("--prompt must not be empty.")

    config = load_config()
    client = LLMClient(config.api_key)
    event_loop = asyncio.new_event_loop()
    session: PromptSession[str] = PromptSession()
    console = Console()
    history: list[dict[str, Any]] = []
    transcript: list[dict[str, Any]] = []
    session_started_at = datetime.now(timezone.utc)
    total_tokens_used = 0
    token_usage_estimated = False
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

    permission_manager = TerminalPermissionHandler(
        project_root=project_root,
        prompt=lambda message: session.prompt(message),
        show_diff=show_diff,
        yolo=arguments.yolo,
    )
    agent_session = AgentSession(
        project_root=project_root,
        tool_registry=tool_registry,
        permission_handler=permission_manager,
        context_manager=context_manager,
        messages=history,
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
        """Consume the typed async event stream using terminal rendering."""
        nonlocal total_tokens_used, token_usage_estimated
        console.print("Claude> ", end="", markup=False)

        def show_tool_call(event: ToolCallEvent) -> None:
            """Render a requested tool call."""
            name = event.name
            arguments = event.args
            argument_text = ", ".join(str(value) for value in arguments.values())
            console.print()
            console.print(f"→ {name}({argument_text})", markup=False)
            console.print("Claude> ", end="", markup=False)

        async def consume_events() -> None:
            """Translate agent events into the existing terminal experience."""
            nonlocal total_tokens_used, token_usage_estimated
            cancellation_event = asyncio.Event()
            async for event in run_agent_events(
                messages=messages,
                llm_client=client,
                tool_registry=active_registry,
                system_prompt=system_prompt,
                permission_handler=agent_session.permission_handler,
                context_manager=agent_session.context_manager,
                cancellation_event=cancellation_event,
                on_message=lambda message: transcript.append(dict(message)),
            ):
                if isinstance(event, TextDeltaEvent):
                    console.print(
                        event.text, end="", markup=False, highlight=False
                    )
                elif isinstance(event, ToolCallEvent):
                    show_tool_call(event)
                elif isinstance(event, PermissionRequestEvent):
                    continue
                elif isinstance(event, UsageEvent):
                    total_tokens_used += event.input_tokens + event.output_tokens
                    token_usage_estimated = (
                        token_usage_estimated or event.estimated
                    )
                    estimate_marker = "~" if event.estimated else ""
                    console.print(
                        f"[dim]tokens {estimate_marker}"
                        f"{event.input_tokens:,}/"
                        f"{agent_session.context_manager.model_context_window:,}"
                        f" ({event.context_percent:.1f}% context)[/dim]"
                    )
                elif isinstance(event, ErrorEvent):
                    if "maximum of" in event.message:
                        raise RuntimeError(event.message)
                    console.print(f"[red]{event.message}[/red]")
                elif isinstance(event, DoneEvent) and event.cancelled:
                    console.print("[yellow]Turn cancelled.[/yellow]")

        event_loop.run_until_complete(consume_events())
        console.print()

    def append_user_message(
        messages: list[dict[str, Any]], content: str
    ) -> None:
        """Add a user message to the active history and the full transcript."""
        message = {"role": "user", "content": content}
        messages.append(message)
        transcript.append(dict(message))

    try:
        if arguments.prompt is not None:
            append_user_message(history, arguments.prompt)
            run_turn(
                history,
                tool_registry,
                build_system_prompt(project_root, project_instructions),
            )
            console.print(
                f"EVAL_TOKENS_USED={total_tokens_used} "
                f"EVAL_TOKENS_ESTIMATED={str(token_usage_estimated).lower()}"
            )
        else:
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
                    append_user_message(history, INIT_INSTRUCTIONS)
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
                    append_user_message(plan_messages, task)
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
                                append_user_message(
                                    history,
                                    (
                                        "I approve the plan above. Execute it now, "
                                        "asking for permission before mutating tools."
                                    ),
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
                                    append_user_message(plan_messages, revision)
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

                append_user_message(history, user_input)
                run_turn(
                    history,
                    tool_registry,
                    build_system_prompt(project_root, project_instructions),
                )
    finally:
        try:
            mcp_client.close()
        finally:
            try:
                close_client = getattr(client, "aclose", None)
                if close_client is not None:
                    event_loop.run_until_complete(close_client())
            finally:
                try:
                    append_session_log(
                        transcript, project_root, session_started_at
                    )
                finally:
                    event_loop.close()


if __name__ == "__main__":
    main()
