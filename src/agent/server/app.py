"""Authenticated FastAPI application for local agent sessions."""

import asyncio
import json
import secrets
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from openai import OpenAIError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agent.tools import (
    BashTool,
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    ToolRegistry,
    WriteFileTool,
)
from agent.server.database import create_database, get_session_factory
from agent.server.models import Chat, Memory, Message, Project, Setting, utc_now
from claude_code.config import load_config
from claude_code.context import ContextManager
from claude_code.events import AgentEvent, DoneEvent, UsageEvent
from claude_code.llm import LLMClient
from claude_code.loop import run_agent_events
from claude_code.permissions import PermissionDecision, WebPermissionHandler
from claude_code.session import AgentSession

BASE_SYSTEM_PROMPT = "You are a helpful coding assistant."
FRONTEND_DEV_ORIGIN = "http://localhost:5173"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    root_path: str
    instructions: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    root_path: str | None = None
    instructions: str | None = None


class ChatCreate(BaseModel):
    project_id: int | None = None
    title: str = "New chat"


class ChatRename(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class ChatPin(BaseModel):
    pinned: bool


class UserMessageCreate(BaseModel):
    content: str = Field(min_length=1)


class MemoryCreate(BaseModel):
    scope: str
    project_id: int | None = None
    content: str = Field(min_length=1)


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1)


class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


class PermissionSubmission(BaseModel):
    decision: PermissionDecision


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectRead(ORMModel):
    id: int
    name: str
    root_path: str
    instructions: str
    created_at: datetime


class ChatRead(ORMModel):
    id: int
    project_id: int | None
    title: str
    created_at: datetime
    updated_at: datetime
    pinned: bool


class MessageRead(ORMModel):
    id: int
    chat_id: int
    role: str
    content: Any
    created_at: datetime
    input_tokens: int | None
    output_tokens: int | None


class MemoryRead(ORMModel):
    id: int
    scope: str
    project_id: int | None
    content: str
    created_at: datetime
    updated_at: datetime


def create_app(
    database_url: str | None = None,
    access_token: str | None = None,
    llm_factory: Callable[[], Any] | None = None,
    title_generator: Callable[[Any, str], str] | None = None,
    frontend_origin: str = FRONTEND_DEV_ORIGIN,
) -> FastAPI:
    """Build an app with injectable database and model dependencies."""
    engine, session_factory = create_database(database_url)
    app = FastAPI(
        title="Shree Agent API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Access-Token"],
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.access_token = access_token or secrets.token_urlsafe(32)
    app.state.llm_factory = llm_factory or _default_llm_factory
    app.state.title_generator = title_generator or _default_title_generator
    app.state.running_turns: dict[int, asyncio.Event] = {}
    app.state.running_turn_loops: dict[int, asyncio.AbstractEventLoop] = {}
    app.state.chat_sessions: dict[int, AgentSession] = {}
    app.state.context_usage: dict[int, dict[str, Any]] = {}

    def cancel_active_turn(chat_id: int) -> bool:
        cancellation_event = app.state.running_turns.get(chat_id)
        if cancellation_event is None:
            return False
        loop = app.state.running_turn_loops.get(chat_id)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(cancellation_event.set)
            return True
        cancellation_event.set()
        return True

    def require_access_token(
        authorization: str | None = Header(default=None),
        x_access_token: str | None = Header(default=None, alias="X-Access-Token"),
        token: str | None = Query(default=None),
    ) -> None:
        supplied = x_access_token or token
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:]
        if not supplied or not secrets.compare_digest(
            supplied, app.state.access_token
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing access token.")

    def db_session():
        yield from get_session_factory(session_factory)

    @app.get("/api/health", dependencies=[Depends(require_access_token)])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/chats",
        response_model=list[ChatRead],
        dependencies=[Depends(require_access_token)],
    )
    def list_chats(
        search: str | None = None,
        db: Session = Depends(db_session),
    ) -> list[Chat]:
        statement = select(Chat)
        if search:
            statement = statement.where(Chat.title.ilike(f"%{search}%"))
        return list(db.scalars(statement.order_by(Chat.pinned.desc(), Chat.updated_at.desc())))

    @app.post(
        "/api/chats",
        response_model=ChatRead,
        status_code=201,
        dependencies=[Depends(require_access_token)],
    )
    def create_chat(
        payload: ChatCreate,
        db: Session = Depends(db_session),
    ) -> Chat:
        if payload.project_id is not None and db.get(Project, payload.project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        chat = Chat(project_id=payload.project_id, title=payload.title)
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return chat

    @app.patch(
        "/api/chats/{chat_id}",
        response_model=ChatRead,
        dependencies=[Depends(require_access_token)],
    )
    def rename_chat(
        chat_id: int,
        payload: ChatRename,
        db: Session = Depends(db_session),
    ) -> Chat:
        chat = _get_or_404(db, Chat, chat_id, "Chat")
        chat.title = payload.title
        chat.updated_at = utc_now()
        db.commit()
        db.refresh(chat)
        return chat

    @app.put(
        "/api/chats/{chat_id}/pin",
        response_model=ChatRead,
        dependencies=[Depends(require_access_token)],
    )
    def pin_chat(
        chat_id: int,
        payload: ChatPin,
        db: Session = Depends(db_session),
    ) -> Chat:
        chat = _get_or_404(db, Chat, chat_id, "Chat")
        chat.pinned = payload.pinned
        chat.updated_at = utc_now()
        db.commit()
        db.refresh(chat)
        return chat

    @app.delete(
        "/api/chats/{chat_id}",
        status_code=204,
        dependencies=[Depends(require_access_token)],
    )
    def delete_chat(chat_id: int, db: Session = Depends(db_session)) -> Response:
        chat = _get_or_404(db, Chat, chat_id, "Chat")
        if chat_id in app.state.running_turns:
            cancel_active_turn(chat_id)
        db.delete(chat)
        db.commit()
        app.state.chat_sessions.pop(chat_id, None)
        app.state.context_usage.pop(chat_id, None)
        return Response(status_code=204)

    @app.get(
        "/api/chats/{chat_id}/export",
        dependencies=[Depends(require_access_token)],
    )
    def export_chat(chat_id: int, db: Session = Depends(db_session)) -> Response:
        chat = _get_or_404(db, Chat, chat_id, "Chat")
        lines = [f"# {chat.title}", ""]
        for message in db.scalars(
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(Message.id)
        ):
            lines.extend(
                [
                    f"## {message.role.title()}",
                    "",
                    _format_markdown_content(message.content),
                    "",
                ]
            )
        return Response(
            content="\n".join(lines),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="chat-{chat_id}.md"'
            },
        )

    @app.get(
        "/api/chats/{chat_id}/messages",
        response_model=list[MessageRead],
        dependencies=[Depends(require_access_token)],
    )
    def list_messages(
        chat_id: int,
        db: Session = Depends(db_session),
    ) -> list[Message]:
        _get_or_404(db, Chat, chat_id, "Chat")
        return list(
            db.scalars(
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.id)
            )
        )

    @app.post(
        "/api/chats/{chat_id}/messages",
        dependencies=[Depends(require_access_token)],
    )
    async def send_message(
        chat_id: int,
        payload: UserMessageCreate,
        db: Session = Depends(db_session),
    ) -> StreamingResponse:
        chat = _get_or_404(db, Chat, chat_id, "Chat")
        if chat_id in app.state.running_turns:
            raise HTTPException(status_code=409, detail="A turn is already running.")
        first_message = db.scalar(
            select(Message.id).where(Message.chat_id == chat_id).limit(1)
        ) is None
        user_message = Message(
            chat_id=chat_id, role="user", content=payload.content
        )
        db.add(user_message)
        chat.updated_at = utc_now()
        db.commit()

        llm_client = app.state.llm_factory()
        if first_message and chat.title == "New chat":
            try:
                generated_title = await asyncio.to_thread(
                    app.state.title_generator, llm_client, payload.content
                )
                chat.title = generated_title[:300] or "New chat"
                chat.updated_at = utc_now()
                db.commit()
            except (OpenAIError, ValueError) as error:
                db.rollback()
                close_client = getattr(llm_client, "aclose", None)
                if close_client is not None:
                    await close_client()
                raise HTTPException(
                    status_code=502, detail="Unable to generate chat title."
                ) from error

        if chat.project_id is not None:
            project = _get_or_404(db, Project, chat.project_id, "Project")
            project_root = Path(project.root_path)
        else:
            project = None
            project_root = Path.cwd().resolve()
        registry = _build_tool_registry(project_root)
        permission_handler = app.state.chat_sessions.get(chat_id)
        if permission_handler is None:
            handler = WebPermissionHandler(project_root)
        else:
            handler = permission_handler.permission_handler
            if permission_handler.project_root != project_root:
                handler = WebPermissionHandler(project_root)
        context_manager = ContextManager()
        messages = [
            (
                message.content
                if isinstance(message.content, dict)
                else {"role": message.role, "content": message.content}
            )
            for message in db.scalars(
                select(Message)
                .where(Message.chat_id == chat_id, Message.role != "system")
                .order_by(Message.id)
            )
        ]
        session = AgentSession(
            project_root=project_root,
            tool_registry=registry,
            permission_handler=handler,
            context_manager=context_manager,
            messages=messages,
        )
        app.state.chat_sessions[chat_id] = session
        cancellation_event = asyncio.Event()
        app.state.running_turns[chat_id] = cancellation_event
        app.state.running_turn_loops[chat_id] = asyncio.get_running_loop()

        async def event_stream():
            latest_assistant: Message | None = None

            def persist_message(message: dict[str, Any]) -> None:
                nonlocal latest_assistant
                row = Message(
                    chat_id=chat_id,
                    role=str(message["role"]),
                    content=message,
                )
                db.add(row)
                if message["role"] == "assistant":
                    latest_assistant = row
                chat.updated_at = utc_now()
                db.commit()

            try:
                global_memories = list(
                    db.scalars(
                        select(Memory).where(
                            Memory.scope == "global",
                            Memory.project_id.is_(None),
                        )
                    )
                )
                prompt = _build_system_prompt(
                    project,
                    global_memories,
                    project_root,
                )
                async for event in run_agent_events(
                    messages=session.messages,
                    llm_client=llm_client,
                    tool_registry=session.tool_registry,
                    system_prompt=prompt,
                    permission_handler=session.permission_handler,
                    context_manager=session.context_manager,
                    cancellation_event=cancellation_event,
                    on_message=persist_message,
                ):
                    if isinstance(event, UsageEvent):
                        if latest_assistant is not None:
                            latest_assistant.input_tokens = (
                                latest_assistant.input_tokens or 0
                            ) + event.input_tokens
                            latest_assistant.output_tokens = (
                                latest_assistant.output_tokens or 0
                            ) + event.output_tokens
                            db.commit()
                        app.state.context_usage[chat_id] = {
                            "input_tokens": event.input_tokens,
                            "output_tokens": event.output_tokens,
                            "context_percent": event.context_percent,
                            "estimated": event.estimated,
                            "output_estimated": event.output_estimated,
                        }
                    yield _sse(event)
            finally:
                app.state.running_turns.pop(chat_id, None)
                app.state.running_turn_loops.pop(chat_id, None)
                close_client = getattr(llm_client, "aclose", None)
                if close_client is not None:
                    await close_client()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/chats/{chat_id}/stop",
        dependencies=[Depends(require_access_token)],
    )
    def stop_chat(chat_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
        _get_or_404(db, Chat, chat_id, "Chat")
        if not cancel_active_turn(chat_id):
            return {"stopping": False}
        return {"stopping": True}

    @app.post(
        "/api/permissions/{request_id}",
        dependencies=[Depends(require_access_token)],
    )
    async def submit_permission(
        request_id: str,
        payload: PermissionSubmission,
    ) -> dict[str, str]:
        for session in app.state.chat_sessions.values():
            try:
                session.permission_handler.submit_decision(
                    request_id, payload.decision
                )
                return {"status": "submitted"}
            except ValueError:
                continue
        raise HTTPException(status_code=404, detail="Permission request not found.")

    @app.get(
        "/api/context/{chat_id}",
        dependencies=[Depends(require_access_token)],
    )
    def get_context(chat_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
        _get_or_404(db, Chat, chat_id, "Chat")
        usage = app.state.context_usage.get(chat_id)
        if usage is None:
            serialized = db.scalars(
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.id)
            ).all()
            character_count = len(
                json.dumps(
                    [message.content for message in serialized],
                    ensure_ascii=False,
                    default=str,
                )
            )
            input_tokens = (character_count + 3) // 4
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": 0,
                "context_percent": input_tokens / 131_072 * 100,
                "estimated": True,
                "output_estimated": True,
            }
        return usage

    @app.get(
        "/api/projects",
        response_model=list[ProjectRead],
        dependencies=[Depends(require_access_token)],
    )
    def list_projects(db: Session = Depends(db_session)) -> list[Project]:
        return list(db.scalars(select(Project).order_by(Project.name)))

    @app.post(
        "/api/projects",
        response_model=ProjectRead,
        status_code=201,
        dependencies=[Depends(require_access_token)],
    )
    def create_project(
        payload: ProjectCreate,
        db: Session = Depends(db_session),
    ) -> Project:
        project = Project(
            name=payload.name,
            root_path=_validate_project_root(payload.root_path),
            instructions=payload.instructions,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project

    @app.get(
        "/api/projects/{project_id}",
        response_model=ProjectRead,
        dependencies=[Depends(require_access_token)],
    )
    def get_project(project_id: int, db: Session = Depends(db_session)) -> Project:
        return _get_or_404(db, Project, project_id, "Project")

    @app.patch(
        "/api/projects/{project_id}",
        response_model=ProjectRead,
        dependencies=[Depends(require_access_token)],
    )
    def update_project(
        project_id: int,
        payload: ProjectUpdate,
        db: Session = Depends(db_session),
    ) -> Project:
        project = _get_or_404(db, Project, project_id, "Project")
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key == "root_path" and value is not None:
                value = _validate_project_root(value)
            setattr(project, key, value)
        db.commit()
        db.refresh(project)
        return project

    @app.delete(
        "/api/projects/{project_id}",
        status_code=204,
        dependencies=[Depends(require_access_token)],
    )
    def delete_project(
        project_id: int,
        db: Session = Depends(db_session),
    ) -> Response:
        project = _get_or_404(db, Project, project_id, "Project")
        db.delete(project)
        db.commit()
        for chat_id, session in list(app.state.chat_sessions.items()):
            if session.project_root == Path(project.root_path):
                app.state.chat_sessions.pop(chat_id, None)
        return Response(status_code=204)

    @app.get(
        "/api/memory",
        response_model=list[MemoryRead],
        dependencies=[Depends(require_access_token)],
    )
    def list_memory(
        scope: str | None = None,
        project_id: int | None = None,
        db: Session = Depends(db_session),
    ) -> list[Memory]:
        statement = select(Memory)
        if scope is not None:
            statement = statement.where(Memory.scope == scope)
        if project_id is not None:
            statement = statement.where(Memory.project_id == project_id)
        return list(db.scalars(statement.order_by(Memory.updated_at.desc())))

    @app.post(
        "/api/memory",
        response_model=MemoryRead,
        status_code=201,
        dependencies=[Depends(require_access_token)],
    )
    def create_memory(
        payload: MemoryCreate,
        db: Session = Depends(db_session),
    ) -> Memory:
        _validate_memory_scope(db, payload.scope, payload.project_id)
        memory = Memory(
            scope=payload.scope,
            project_id=payload.project_id,
            content=payload.content,
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)
        return memory

    @app.patch(
        "/api/memory/{memory_id}",
        response_model=MemoryRead,
        dependencies=[Depends(require_access_token)],
    )
    def update_memory(
        memory_id: int,
        payload: MemoryUpdate,
        db: Session = Depends(db_session),
    ) -> Memory:
        memory = _get_or_404(db, Memory, memory_id, "Memory")
        memory.content = payload.content
        memory.updated_at = utc_now()
        db.commit()
        db.refresh(memory)
        return memory

    @app.delete(
        "/api/memory/{memory_id}",
        status_code=204,
        dependencies=[Depends(require_access_token)],
    )
    def delete_memory(memory_id: int, db: Session = Depends(db_session)) -> Response:
        memory = _get_or_404(db, Memory, memory_id, "Memory")
        db.delete(memory)
        db.commit()
        return Response(status_code=204)

    @app.get(
        "/api/settings",
        dependencies=[Depends(require_access_token)],
    )
    def get_settings(db: Session = Depends(db_session)) -> dict[str, Any]:
        return {setting.key: setting.value for setting in db.scalars(select(Setting))}

    @app.put(
        "/api/settings",
        dependencies=[Depends(require_access_token)],
    )
    def update_settings(
        payload: SettingsUpdate,
        db: Session = Depends(db_session),
    ) -> dict[str, Any]:
        for key, value in payload.settings.items():
            setting = db.get(Setting, key)
            if setting is None:
                db.add(Setting(key=key, value=value))
            else:
                setting.value = value
        db.commit()
        return {setting.key: setting.value for setting in db.scalars(select(Setting))}

    return app


def _default_llm_factory() -> LLMClient:
    return LLMClient(load_config().api_key)


def _default_title_generator(llm_client: Any, prompt: str) -> str:
    return llm_client.generate_title(prompt)


def _build_tool_registry(project_root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(project_root),
        ListDirTool(project_root),
        GrepTool(project_root),
        GlobTool(project_root),
        WriteFileTool(project_root),
        EditFileTool(project_root),
        BashTool(project_root),
    ):
        registry.register(tool)
    return registry


def _build_system_prompt(
    project: Project | None,
    global_memories: list[Memory],
    project_root: Path,
) -> str:
    sections = [BASE_SYSTEM_PROMPT]
    if global_memories:
        sections.append(
            "Global memory\n"
            + "\n".join(f"- {memory.content}" for memory in global_memories)
        )
    if project and project.instructions.strip():
        sections.append(f"Project instructions\n{project.instructions.strip()}")
    if project:
        project_memories = [
            memory.content
            for memory in project.memories
        ]
        if project_memories:
            sections.append(
                "Project memory\n"
                + "\n".join(f"- {content}" for content in project_memories)
            )
    agent_instructions = project_root / "AGENTS.md"
    if agent_instructions.is_file():
        content = agent_instructions.read_text(encoding="utf-8").strip()
        if content:
            sections.append(f"Project AGENTS.md\n{content}")
    return "\n\n".join(sections)


def _sse(event: AgentEvent) -> str:
    """Serialize a typed agent event as a Server-Sent Event frame."""
    if not is_dataclass(event):
        raise TypeError(f"Unsupported agent event: {type(event).__name__}")
    payload = json.dumps(asdict(event), ensure_ascii=False)
    return (
        f"event: {event.type}\n"
        f'"data": {payload}\n'
        f"data: {payload}\n\n"
    )


def _validate_project_root(root_path: str) -> str:
    """Resolve and validate a project directory."""
    path = Path(root_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise HTTPException(
            status_code=422,
            detail="Project root_path must point to an existing directory.",
        )
    return str(path)


def _validate_memory_scope(
    db: Session,
    scope: str,
    project_id: int | None,
) -> None:
    if scope == "global":
        if project_id is not None:
            raise HTTPException(
                status_code=422,
                detail="Global memory must not specify project_id.",
            )
        return
    if scope == "project":
        if project_id is None or db.get(Project, project_id) is None:
            raise HTTPException(
                status_code=422,
                detail="Project memory requires a valid project_id.",
            )
        return
    raise HTTPException(
        status_code=422, detail="Memory scope must be 'global' or 'project'."
    )


def _get_or_404(
    db: Session,
    model: type[Any],
    identifier: int,
    name: str,
) -> Any:
    record = db.get(model, identifier)
    if record is None:
        raise HTTPException(status_code=404, detail=f"{name} not found.")
    return record


def _format_markdown_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return f"```json\n{json.dumps(content, indent=2, ensure_ascii=False)}\n```"
