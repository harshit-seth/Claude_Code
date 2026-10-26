"""FastAPI integration tests for the local agent backend."""

import asyncio
import json
import queue
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent.server.app import create_app
from claude_code.llm import LLMResponse, ToolCall


class FakeLLMClient:
    def __init__(self, response_factory=None) -> None:
        self._response_factory = response_factory
        self.responses = []

    def generate_title(self, prompt: str) -> str:
        return f"Title: {prompt[:20]}"

    async def stream_response_async(
        self,
        messages,
        system_prompt,
        tools,
        on_text,
        cancellation_event,
    ) -> LLMResponse:
        self.responses.append((list(messages), system_prompt))
        if self._response_factory is not None:
            response = self._response_factory(messages, tools)
        else:
            response = LLMResponse(
                content="Hello from the fake agent.",
                tool_calls=[],
                finish_reason="stop",
            )
        if response.content:
            on_text(response.content)
        return response

    async def aclose(self) -> None:
        return None


def _client(tmp_path: Path, fake_llm: FakeLLMClient | None = None):
    token = "test-access-token"
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        access_token=token,
        llm_factory=lambda: fake_llm or FakeLLMClient(),
        title_generator=lambda llm_client, prompt: llm_client.generate_title(prompt),
    )
    return TestClient(app), {"X-Access-Token": token}


def test_api_requires_token_and_limits_cors_to_frontend_origin(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)

    assert client.get("/api/chats").status_code == 401
    assert client.get("/api/chats", headers=headers).status_code == 200
    allowed = client.options(
        "/api/chats",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.options(
        "/api/chats",
        headers={
            "Origin": "https://example.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


def test_chat_crud_search_messages_pin_delete_and_markdown_export(
    tmp_path: Path,
) -> None:
    client, headers = _client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    project_response = client.post(
        "/api/projects",
        headers=headers,
        json={
            "name": "Demo",
            "root_path": str(project_root),
            "instructions": "Use pytest.",
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()

    invalid_project = client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Missing", "root_path": str(tmp_path / "missing")},
    )
    assert invalid_project.status_code == 422

    created = client.post(
        "/api/chats",
        headers=headers,
        json={"project_id": project["id"], "title": "Alpha"},
    )
    assert created.status_code == 201
    chat_id = created.json()["id"]
    assert client.patch(
        f"/api/chats/{chat_id}",
        headers=headers,
        json={"title": "Renamed chat"},
    ).json()["title"] == "Renamed chat"
    assert client.put(
        f"/api/chats/{chat_id}/pin",
        headers=headers,
        json={"pinned": True},
    ).json()["pinned"] is True
    assert client.get(
        "/api/chats?search=Renamed",
        headers=headers,
    ).json()[0]["id"] == chat_id

    messages = client.get(
        f"/api/chats/{chat_id}/messages",
        headers=headers,
    )
    assert messages.status_code == 200
    assert messages.json() == []
    exported = client.get(
        f"/api/chats/{chat_id}/export",
        headers=headers,
    )
    assert exported.status_code == 200
    assert "# Renamed chat" in exported.text

    assert client.delete(
        f"/api/chats/{chat_id}",
        headers=headers,
    ).status_code == 204
    assert client.get(
        f"/api/chats/{chat_id}/messages",
        headers=headers,
    ).status_code == 404


def test_projects_memory_and_settings_crud(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    project = client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Repo", "root_path": str(root)},
    ).json()

    assert client.get(
        f"/api/projects/{project['id']}",
        headers=headers,
    ).json()["name"] == "Repo"
    assert client.patch(
        f"/api/projects/{project['id']}",
        headers=headers,
        json={"root_path": str(tmp_path / "nope")},
    ).status_code == 422

    global_memory = client.post(
        "/api/memory",
        headers=headers,
        json={"scope": "global", "content": "Prefer concise answers."},
    )
    project_memory = client.post(
        "/api/memory",
        headers=headers,
        json={
            "scope": "project",
            "project_id": project["id"],
            "content": "Run pytest.",
        },
    )
    assert global_memory.status_code == 201
    assert project_memory.status_code == 201
    memory_id = project_memory.json()["id"]
    assert client.patch(
        f"/api/memory/{memory_id}",
        headers=headers,
        json={"content": "Run the full test suite."},
    ).json()["content"] == "Run the full test suite."
    assert len(client.get("/api/memory", headers=headers).json()) == 2
    assert client.delete(
        f"/api/memory/{memory_id}",
        headers=headers,
    ).status_code == 204

    settings = client.put(
        "/api/settings",
        headers=headers,
        json={"settings": {"theme": "dark", "model": "fake-model"}},
    )
    assert settings.json() == {"theme": "dark", "model": "fake-model"}
    assert client.get("/api/settings", headers=headers).json() == settings.json()


def test_chat_message_sse_persists_full_turn_context_and_title(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("Use ruff.", encoding="utf-8")
    fake_llm = FakeLLMClient()
    client, headers = _client(tmp_path, fake_llm)
    project = client.post(
        "/api/projects",
        headers=headers,
        json={
            "name": "Repo",
            "root_path": str(project_root),
            "instructions": "Use pytest.",
        },
    ).json()
    client.post(
        "/api/memory",
        headers=headers,
        json={"scope": "global", "content": "Prefer concise answers."},
    )
    client.post(
        "/api/memory",
        headers=headers,
        json={
            "scope": "project",
            "project_id": project["id"],
            "content": "Run tests before committing.",
        },
    )
    chat_id = client.post(
        "/api/chats",
        headers=headers,
        json={"project_id": project["id"]},
    ).json()["id"]

    with client.stream(
        "POST",
        f"/api/chats/{chat_id}/messages",
        headers=headers,
        json={"content": "How do I test?"},
    ) as response:
        body = "".join(response.iter_text())

    events = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert [event["type"] for event in events] == [
        "text_delta",
        "usage",
        "done",
    ]
    assert events[-1]["cancelled"] is False
    messages = client.get(
        f"/api/chats/{chat_id}/messages",
        headers=headers,
    ).json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "How do I test?"
    assert messages[1]["content"]["content"] == "Hello from the fake agent."
    assert messages[1]["input_tokens"] is not None
    assert messages[1]["output_tokens"] is not None
    prompt = fake_llm.responses[0][1]
    assert "Prefer concise answers." in prompt
    assert "Use pytest." in prompt
    assert "Run tests before committing." in prompt
    assert "Use ruff." in prompt
    assert client.get(
        f"/api/context/{chat_id}",
        headers=headers,
    ).json()["input_tokens"] > 0
    chat = client.get("/api/chats", headers=headers).json()[0]
    assert chat["title"] == "Title: How do I test?"


def test_sse_permission_request_can_be_approved_externally(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "permission-project"
    project_root.mkdir()

    def response_factory(messages, _tools):
        if messages and messages[-1].get("role") == "tool":
            return LLMResponse("Write completed.", [], "stop")
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    "write-call",
                    "write_file",
                    {"path": "new.txt", "content": "created by agent"},
                )
            ],
            finish_reason="tool_calls",
        )

    token = "permission-token"
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'permission.db').as_posix()}",
        access_token=token,
        llm_factory=lambda: FakeLLMClient(response_factory=response_factory),
        title_generator=lambda llm_client, prompt: llm_client.generate_title(prompt),
    )
    client = TestClient(app)
    decision_client = TestClient(app)
    headers = {"X-Access-Token": token}
    project = client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Permission", "root_path": str(project_root)},
    ).json()
    chat_id = client.post(
        "/api/chats",
        headers=headers,
        json={"project_id": project["id"]},
    ).json()["id"]
    stream_errors: queue.Queue[BaseException] = queue.Queue()
    result: dict[str, str] = {}

    def read_stream() -> None:
        stream_lines = []
        try:
            with client.stream(
                "POST",
                f"/api/chats/{chat_id}/messages",
                headers=headers,
                json={"content": "Create a file."},
            ) as response:
                for line in response.iter_lines():
                    stream_lines.append(line)
                result["body"] = "\n".join(stream_lines)
        except BaseException as error:
            stream_errors.put(error)

    stream_thread = threading.Thread(target=read_stream)
    stream_thread.start()
    deadline = time.monotonic() + 10
    pending_ids: tuple[str, ...] = ()
    while time.monotonic() < deadline:
        agent_session = app.state.chat_sessions.get(chat_id)
        if agent_session is not None:
            pending_ids = agent_session.permission_handler.pending_request_ids
            if pending_ids:
                break
        time.sleep(0.01)
    assert pending_ids
    submitted = decision_client.post(
        f"/api/permissions/{pending_ids[0]}",
        headers=headers,
        json={"decision": "allow_once"},
    )
    stream_thread.join(timeout=10)

    if stream_thread.is_alive():
        client.close()
        decision_client.close()
        raise AssertionError("SSE stream did not finish after permission approval.")
    if not stream_errors.empty():
        raise stream_errors.get()
    assert submitted.status_code == 200
    assert result.get("body") is not None
    assert "event: permission_request" in result["body"]
    assert (project_root / "new.txt").read_text(encoding="utf-8") == "created by agent"


def test_stop_endpoint_cancels_the_active_chat_turn(tmp_path: Path) -> None:
    token = "stop-token"

    class WaitingLLMClient(FakeLLMClient):
        async def stream_response_async(
            self,
            messages,
            system_prompt,
            tools,
            on_text,
            cancellation_event,
        ):
            await cancellation_event.wait()
            raise asyncio.CancelledError

    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'stop.db').as_posix()}",
        access_token=token,
        llm_factory=WaitingLLMClient,
        title_generator=lambda llm_client, prompt: llm_client.generate_title(prompt),
    )
    client = TestClient(app)
    stop_client = TestClient(app)
    headers = {"X-Access-Token": token}
    chat_id = client.post("/api/chats", headers=headers, json={}).json()["id"]
    stream_errors: queue.Queue[BaseException] = queue.Queue()
    result: dict[str, str] = {}

    def read_stream() -> None:
        try:
            with client.stream(
                "POST",
                f"/api/chats/{chat_id}/messages",
                headers=headers,
                json={"content": "Wait forever"},
            ) as response:
                result["body"] = "".join(response.iter_text())
        except BaseException as error:
            stream_errors.put(error)

    stream_thread = threading.Thread(target=read_stream)
    stream_thread.start()
    deadline = time.monotonic() + 10
    while chat_id not in app.state.running_turns and time.monotonic() < deadline:
        time.sleep(0.01)
    assert chat_id in app.state.running_turns
    stopped = stop_client.post(
        f"/api/chats/{chat_id}/stop",
        headers=headers,
    )
    stream_thread.join(timeout=10)

    assert stopped.status_code == 200
    assert stopped.json() == {"stopping": True}
    assert not stream_thread.is_alive()
    if not stream_errors.empty():
        raise stream_errors.get()
    assert '"data": {"type": "done", "cancelled": true}' in result["body"]
