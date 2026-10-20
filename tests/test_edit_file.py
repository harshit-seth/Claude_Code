"""Tests for exact project file editing."""

from pathlib import Path

import pytest

from agent.tools import EditFileTool


def test_edit_file_replaces_one_exact_match(tmp_path: Path) -> None:
    target = tmp_path / "example.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    result = EditFileTool(tmp_path).run(
        path="example.py",
        old_string="VALUE = 1",
        new_string="VALUE = 2",
    )

    assert result == "Updated example.py."
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_edit_file_fails_when_old_string_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "example.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="old_string was not found"):
        EditFileTool(tmp_path).run(
            path="example.py",
            old_string="MISSING",
            new_string="VALUE = 2",
        )
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_edit_file_fails_when_old_string_is_ambiguous(tmp_path: Path) -> None:
    target = tmp_path / "example.py"
    target.write_text("VALUE = 1\nVALUE = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="appears 2 times"):
        EditFileTool(tmp_path).run(
            path="example.py",
            old_string="VALUE = 1",
            new_string="VALUE = 2",
        )
    assert target.read_text(encoding="utf-8") == "VALUE = 1\nVALUE = 1\n"


def test_edit_file_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the project root"):
        EditFileTool(tmp_path).run(
            path="../../outside.txt",
            old_string="old",
            new_string="new",
        )
