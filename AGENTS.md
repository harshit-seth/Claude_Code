# Claude code

A terminal coding agent written in Python, built as a portfolio project for SDE1 interviews.

## Stack

- Python 3.10 or newer
- `src/` package layout with the `claude_code` import package
- Anthropic SDK for Claude API access
- Rich, prompt_toolkit, and python-dotenv
- pytest for development tests

## Project rules

- Keep functions small, use type hints, and write concise docstrings.
- Add a test for every tool.
- Implement tools with the `Tool` base class and register them with
  `ToolRegistry`; filesystem tools must stay within the project root.
- `bash` commands must require explicit user approval before execution.
- Do not add code beyond the requested feature.
- Explain non-obvious design choices in one or two lines.
- Keep the implementation clean, tested, and easy to explain.
- Read `ANTHROPIC_API_KEY` from the environment or a local `.env` file.
- Keep the model selection in the single `MODEL` constant in `config.py`.
- The CLI streams replies and supports `/exit` and `/clear`.

## Run tests

Run the test suite from the project root:

```powershell
python -m pytest
```

Install the package and development dependencies, then start the CLI:

```powershell
python -m pip install -e ".[dev]"
claude-code
```
