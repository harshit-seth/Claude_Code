"""Command-line entry point for the local web server."""

import argparse
import secrets

import uvicorn

from agent.server.app import create_app


def main(argv: list[str] | None = None) -> None:
    """Start the API on loopback with a fresh access token."""
    parser = argparse.ArgumentParser(prog="shree")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser("serve", help="Start the local web API.")
    serve_parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args(argv)

    access_token = secrets.token_urlsafe(32)
    app = create_app(access_token=access_token)
    print(
        "Shree API available at "
        f"http://127.0.0.1:{arguments.port}/api/chats?token={access_token}"
    )
    uvicorn.run(app, host="127.0.0.1", port=arguments.port)
