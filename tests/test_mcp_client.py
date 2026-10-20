"""Integration tests for stdio MCP server connections."""

import json
from pathlib import Path
import sys

from agent.tools import ToolRegistry
from claude_code.mcp_client import MCPClient
from claude_code.permissions import PermissionManager, ToolPermission


def _write_config(project_root: Path, servers: dict[str, object]) -> None:
    config_path = project_root / ".shree" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"mcpServers": servers}),
        encoding="utf-8",
    )


def test_starts_stdio_server_registers_namespaced_tool_and_handles_failures(
    tmp_path: Path,
) -> None:
    server_script = Path(__file__).with_name("mcp_echo_server.py")
    _write_config(
        tmp_path,
        {
            "unavailable": {
                "command": str(tmp_path / "missing-mcp-server.exe"),
                "args": [],
            },
            "echo": {
                "command": sys.executable,
                "args": [str(server_script)],
            },
        },
    )
    registry = ToolRegistry()
    client = MCPClient(tmp_path)

    failures = client.start(registry)
    try:
        assert len(failures) == 1
        assert "MCP server 'unavailable' failed to start" in failures[0]
        definitions = registry.get_definitions()
        assert [definition["function"]["name"] for definition in definitions] == [
            "echo__echo"
        ]
        assert (
            PermissionManager.classify_tool("echo__echo")
            is ToolPermission.MUTATING
        )
        prompts = []

        def ignore_diff(diff: str) -> None:
            assert isinstance(diff, str)

        permission_manager = PermissionManager(
            project_root=tmp_path,
            prompt=lambda prompt: prompts.append(prompt) or "n",
            show_diff=ignore_diff,
        )
        assert permission_manager.authorize("echo__echo", {}) == (
            "User denied tool 'echo__echo'."
        )
        assert prompts == [
            "Allow echo__echo? [y] yes once, [a] always allow this tool "
            "in this session, [n] no: "
        ]
        assert (
            registry.execute("echo__echo", message="hello over MCP")
            == "hello over MCP"
        )
    finally:
        client.close()
