"""Tests for the project file writing tool."""

from pathlib import Path

import pytest

from agent.tools import WriteFileTool


def test_write_file_creates_parent_directories_and_file(tmp_path: Path) -> None:
    result = WriteFileTool(tmp_path).run(path="src/new.py", content="print('ok')\n")

    assert result == "Wrote src/new.py."
    assert (tmp_path / "src" / "new.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_write_file_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")

    WriteFileTool(tmp_path).run(path="existing.txt", content="new")

    assert target.read_text(encoding="utf-8") == "new"


def test_write_file_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the project root"):
        WriteFileTool(tmp_path).run(path="../../outside.txt", content="no")
