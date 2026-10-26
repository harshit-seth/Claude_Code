"""Tests for isolated per-session agent state."""

import asyncio
from pathlib import Path

from agent.tools.base import ToolRegistry
from claude_code.context import ContextManager
from claude_code.permissions import PermissionManager
from claude_code.session import AgentSession


def _permission_manager(project_root: Path, prompt_choice: str) -> PermissionManager:
    return PermissionManager(
        project_root=project_root,
        prompt=lambda _prompt: prompt_choice,
        show_diff=lambda _diff: None,
    )


def test_agent_sessions_keep_roots_histories_and_permission_state_separate(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_permissions = _permission_manager(first_root, "a")
    second_permissions = _permission_manager(second_root, "n")
    first_session = AgentSession(
        first_root,
        ToolRegistry(),
        first_permissions,
        ContextManager(),
    )
    second_session = AgentSession(
        second_root,
        ToolRegistry(),
        second_permissions,
        ContextManager(),
    )
    first_session.messages.append({"role": "user", "content": "first"})

    async def approve_once() -> None:
        await first_permissions.request("bash", {"command": "pytest"})

    asyncio.run(approve_once())

    assert first_session.project_root == first_root.resolve()
    assert second_session.project_root == second_root.resolve()
    assert first_session.messages != second_session.messages
    assert first_permissions.requires_approval("bash") is False
    assert second_permissions.requires_approval("bash") is True
