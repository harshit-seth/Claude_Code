"""Load project-specific instructions for the coding agent."""

from pathlib import Path


def load_project_instructions(project_root: Path) -> str | None:
    """Load AGENT.md from the project root when the file is present."""
    instructions_path = project_root / "AGENT.md"
    if not instructions_path.exists():
        return None
    return instructions_path.read_text(encoding="utf-8").strip()
