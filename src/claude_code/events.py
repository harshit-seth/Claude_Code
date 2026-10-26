"""Typed events emitted by an agent turn."""

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class TextDeltaEvent:
    type: Literal["text_delta"]
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    type: Literal["tool_call"]
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResultEvent:
    type: Literal["tool_result"]
    name: str
    output: str
    is_error: bool


@dataclass(frozen=True)
class PermissionRequestEvent:
    type: Literal["permission_request"]
    id: str
    tool: str
    args: dict[str, Any]
    diff: str | None


@dataclass(frozen=True)
class UsageEvent:
    type: Literal["usage"]
    input_tokens: int
    output_tokens: int
    context_percent: float
    estimated: bool
    output_estimated: bool = True


@dataclass(frozen=True)
class ErrorEvent:
    type: Literal["error"]
    message: str


@dataclass(frozen=True)
class DoneEvent:
    type: Literal["done"]
    cancelled: bool


AgentEvent: TypeAlias = (
    TextDeltaEvent
    | ToolCallEvent
    | ToolResultEvent
    | PermissionRequestEvent
    | UsageEvent
    | ErrorEvent
    | DoneEvent
)
