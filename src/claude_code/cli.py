"""Command-line entry point."""

from anthropic.types import MessageParam
from prompt_toolkit import PromptSession
from rich.console import Console

from claude_code.config import load_config
from claude_code.llm import LLMClient

SYSTEM_PROMPT = "You are a helpful coding assistant."


def main() -> None:
    """Run the interactive coding assistant."""
    config = load_config()
    client = LLMClient(config.api_key)
    session: PromptSession[str] = PromptSession()
    console = Console()
    history: list[MessageParam] = []

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
        response_parts: list[str] = []
        for text in client.stream(history, SYSTEM_PROMPT):
            response_parts.append(text)
            console.print(text, end="", markup=False, highlight=False)
        console.print()
        history.append({"role": "assistant", "content": "".join(response_parts)})
