"""Tests for the tool registry and filesystem tools."""

from pathlib import Path

import pytest

from agent.tools import ListDirTool, ReadFileTool, Tool, ToolRegistry


class EchoTool(Tool):
    """Test tool that returns its message."""

    name = "echo"
    description = "Return the provided message."
    input_schema = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def run(self, **kwargs) -> str:
        return kwargs["message"]


def test_registry_registers_definitions_and_executes_tools() -> None:
    registry = ToolRegistry()
    tool = EchoTool()
    registry.register(tool)

    assert registry.get_definitions() == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Return the provided message.",
                "parameters": tool.input_schema,
            },
        }
    ]
    assert registry.execute("echo", message="hello") == "hello"


def test_registry_returns_errors_and_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    assert registry.execute("missing") == "Error: unknown tool 'missing'."
    assert "Error running tool 'echo'" in registry.execute("echo")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


def test_registry_filter_only_exposes_selected_tools() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(ReadFileTool(Path.cwd()))

    filtered = registry.filtered({"read_file"})

    assert [tool["function"]["name"] for tool in filtered.get_definitions()] == [
        "read_file"
    ]
    assert filtered.execute("echo", message="hello") == "Error: unknown tool 'echo'."


def test_registry_filter_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="Unknown tools"):
        ToolRegistry().filtered({"missing"})


def test_read_file_returns_numbered_contents(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("first\nsecond\n", encoding="utf-8")

    result = ReadFileTool(tmp_path).run(path="sample.txt")

    assert result == "   1 | first\n   2 | second"


def test_read_file_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the project root"):
        ReadFileTool(tmp_path).run(path="../../etc/passwd")


def test_read_file_truncates_long_output_with_note(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 25_000, encoding="utf-8")

    result = ReadFileTool(tmp_path).run(path="large.txt")

    assert len(result) == 20_000
    assert result.endswith("... (output truncated at 20000 characters)")


def test_list_dir_lists_entries_with_directories_first(tmp_path: Path) -> None:
    (tmp_path / "z-file.txt").touch()
    (tmp_path / "folder").mkdir()
    (tmp_path / "A-file.txt").touch()

    result = ListDirTool(tmp_path).run()

    assert result == "folder/\nA-file.txt\nz-file.txt"


def test_list_dir_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the project root"):
        ListDirTool(tmp_path).run(path="../../")
