"""Tests for loading project-specific agent instructions."""

from pathlib import Path

from claude_code.memory import load_project_instructions


def test_load_project_instructions_returns_none_when_file_is_missing(
    tmp_path: Path,
) -> None:
    assert load_project_instructions(tmp_path) is None


def test_load_project_instructions_reads_root_agent_file(tmp_path: Path) -> None:
    (tmp_path / "AGENT.md").write_text("  Use pytest.  \n", encoding="utf-8")

    assert load_project_instructions(tmp_path) == "Use pytest."
