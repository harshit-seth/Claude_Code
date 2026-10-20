"""Small stdio MCP server used by the MCP client integration tests."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo-test")


@server.tool()
def echo(message: str) -> str:
    """Return the given message."""
    return message


if __name__ == "__main__":
    server.run(transport="stdio")
