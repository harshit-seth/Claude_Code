# Claude code

A terminal coding agent written in Python, built as a portfolio project for SDE1 interviews.

## Stack

- Python 3.10 or newer
- `src/` package layout with the `claude_code` import package
- OpenAI SDK for OpenAI-compatible providers configured with a base URL and API key
- Rich, prompt_toolkit, and python-dotenv
- pytest for development tests

## Project rules

- Keep functions small, use type hints, and write concise docstrings.
- Add a test for every tool.
- Do not add code beyond the requested feature.
- Explain non-obvious design choices in one or two lines.
- Keep the implementation clean, tested, and easy to explain.

## Run tests

Run the test suite from the project root:

```powershell
python -m pytest
```
