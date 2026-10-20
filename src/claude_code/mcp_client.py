"""Connect to configured MCP servers and expose their tools to the agent."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent, Tool as MCPToolDefinition

from agent.tools.base import Tool, ToolRegistry

SERVER_START_TIMEOUT_SECONDS = 30


class MCPTool(Tool):
    """Adapter that routes an agent tool call to its MCP server."""

    def __init__(
        self,
        client: MCPClient,
        server_name: str,
        definition: MCPToolDefinition,
    ) -> None:
        self._client = client
        self._server_name = server_name
        self._remote_name = definition.name
        self.name = f"{server_name}__{definition.name}"
        self.description = definition.description or f"MCP tool {definition.name}."
        self.input_schema = definition.inputSchema

    def run(self, **kwargs: Any) -> str:
        """Call the corresponding MCP tool and format its result."""
        return self._client.call_tool(self._server_name, self._remote_name, kwargs)


class MCPClient:
    """Maintain stdio MCP sessions for the lifetime of the agent."""

    def __init__(self, project_root: Path) -> None:
        """Read server configuration from the project root."""
        self._config_path = project_root / ".shree" / "mcp.json"
        self._servers: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools: list[MCPTool] = []
        self._failures: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self, registry: ToolRegistry) -> list[str]:
        """Connect to configured servers, register tools, and report failures."""
        if self._started:
            raise RuntimeError("MCP client has already been started.")
        self._started = True
        if not self._config_path.exists():
            return []

        try:
            self._servers = self._read_server_config()
        except (OSError, json.JSONDecodeError, ValueError) as error:
            return [f"Unable to load MCP configuration: {error}"]
        if not self._servers:
            return list(self._failures)

        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="mcp-client",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

        for tool in self._tools:
            try:
                registry.register(tool)
            except ValueError as error:
                self._failures.append(
                    f"Unable to register MCP tool '{tool.name}': {error}"
                )
        return list(self._failures)

    def close(self) -> None:
        """Close active sessions and stop the MCP event loop."""
        if self._loop is None or self._stop_event is None or self._thread is None:
            return
        self._loop.call_soon_threadsafe(self._stop_event.set)
        self._thread.join()
        self._loop = None
        self._stop_event = None
        self._sessions.clear()

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Run a remote tool on its owning event loop."""
        if server_name not in self._sessions or self._loop is None:
            return f"Error: MCP server '{server_name}' is unavailable."

        future = asyncio.run_coroutine_threadsafe(
            self._call_tool(server_name, tool_name, arguments),
            self._loop,
        )
        result = future.result()
        text_blocks = [
            block.text for block in result.content if isinstance(block, TextContent)
        ]
        if text_blocks:
            rendered = "\n".join(text_blocks)
        elif result.structuredContent is not None:
            rendered = json.dumps(result.structuredContent, ensure_ascii=False)
        else:
            rendered = json.dumps(
                [block.model_dump(mode="json") for block in result.content],
                ensure_ascii=False,
            )
        if result.isError:
            return f"MCP tool error: {rendered}"
        return rendered

    def _read_server_config(self) -> dict[str, dict[str, Any]]:
        """Load and validate the standard mcpServers configuration mapping."""
        with self._config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
        if not isinstance(config, dict):
            raise ValueError("the top-level JSON value must be an object.")
        servers = config.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError("'mcpServers' must be an object.")

        valid_servers: dict[str, dict[str, Any]] = {}
        for name, server in servers.items():
            if not isinstance(name, str) or not name.strip():
                self._failures.append("Ignoring MCP server with an empty name.")
                continue
            if (
                not isinstance(server, dict)
                or not isinstance(server.get("command"), str)
                or not server["command"].strip()
                or not isinstance(server.get("args", []), list)
                or not all(
                    isinstance(argument, str)
                    for argument in server.get("args", [])
                )
            ):
                self._failures.append(
                    f"Ignoring invalid MCP server configuration for '{name}'."
                )
                continue
            valid_servers[name] = server
        return valid_servers

    def _run_event_loop(self) -> None:
        """Run MCP sessions on a dedicated asyncio loop."""
        try:
            asyncio.run(self._serve())
        except Exception as error:
            self._failures.append(f"MCP client stopped unexpectedly: {error}")
            self._ready.set()

    async def _serve(self) -> None:
        """Start each configured server and retain its session until shutdown."""
        async with AsyncExitStack() as stack:
            self._loop = asyncio.get_running_loop()
            self._stop_event = asyncio.Event()
            for server_name, server in self._servers.items():
                server_stack = AsyncExitStack()
                try:
                    parameters = StdioServerParameters(
                        command=server["command"],
                        args=server.get("args", []),
                    )
                    read_stream, write_stream = await asyncio.wait_for(
                        server_stack.enter_async_context(stdio_client(parameters)),
                        timeout=SERVER_START_TIMEOUT_SECONDS,
                    )
                    session = await server_stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    await asyncio.wait_for(
                        session.initialize(),
                        timeout=SERVER_START_TIMEOUT_SECONDS,
                    )
                    result = await asyncio.wait_for(
                        session.list_tools(),
                        timeout=SERVER_START_TIMEOUT_SECONDS,
                    )
                    await stack.enter_async_context(server_stack)
                    self._sessions[server_name] = session
                    self._tools.extend(
                        MCPTool(self, server_name, definition)
                        for definition in result.tools
                    )
                except Exception as error:
                    self._failures.append(
                        f"MCP server '{server_name}' failed to start: {error}"
                    )
                    try:
                        await server_stack.aclose()
                    except Exception as cleanup_error:
                        self._failures.append(
                            f"Unable to clean up MCP server '{server_name}': "
                            f"{cleanup_error}"
                        )
            self._ready.set()
            await self._stop_event.wait()

    async def _call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        """Issue a tool call using the server's active session."""
        return await self._sessions[server_name].call_tool(tool_name, arguments)
