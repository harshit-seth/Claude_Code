"""Tests for the regular-expression search tool."""

import re
from pathlib import Path

import pytest

from agent.tools import GrepTool


def test_grep_returns_file_line_and_text(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("first\nmatch this\nlast\n", encoding="utf-8")

    assert GrepTool(tmp_path).run(pattern="match") == "sample.py:2:match this"


def test_grep_caps_results_at_one_hundred(tmp_path: Path) -> None:
    (tmp_path / "many.txt").write_text("match\n" * 101, encoding="utf-8")

    result = GrepTool(tmp_path).run(pattern="match")
    lines = result.splitlines()

    assert len(lines) == 101
    assert lines[0] == "many.txt:1:match"
    assert lines[99] == "many.txt:100:match"
    assert lines[100] == "... results truncated at 100 matches."


def test_grep_rejects_invalid_regular_expression(tmp_path: Path) -> None:
    with pytest.raises(re.error):
        GrepTool(tmp_path).run(pattern="[")
