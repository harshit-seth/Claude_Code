"""Tests for the project file glob tool."""

from pathlib import Path

import pytest

from agent.tools import GlobTool


def test_glob_finds_matching_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").touch()
    (tmp_path / "README.md").touch()

    assert GlobTool(tmp_path).run(pattern="**/*.py") == "src/main.py"


def test_glob_rejects_patterns_outside_project_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="within the project root"):
        GlobTool(tmp_path).run(pattern="../*.py")


def test_glob_returns_message_when_no_files_match(tmp_path: Path) -> None:
    assert GlobTool(tmp_path).run(pattern="**/*.py") == "No files found."
