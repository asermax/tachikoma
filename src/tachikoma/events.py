from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

StatusCallback = Callable[[str], Awaitable[None]]


@dataclass
class AgentEvent:
    """Base class for all agent events."""


@dataclass
class TextChunk(AgentEvent):
    """A chunk of text from the agent's response stream."""

    text: str


@dataclass
class ToolActivity(AgentEvent):
    """A tool invocation performed by the agent."""

    tool_name: str
    tool_input: dict[str, Any]
    result: str = ""


@dataclass
class Result(AgentEvent):
    """Terminal event marking the end of an agent response."""

    session_id: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None


@dataclass
class Status(AgentEvent):
    """A transient, component-driven status update forwarded by the coordinator.

    Emitted by boundary detection and the pre-processing pipelines via
    ``StatusCallback`` so users see what work is in flight before the agent
    starts streaming its response.
    """

    message: str


@dataclass
class Error(AgentEvent):
    """An error encountered during agent processing."""

    message: str
    recoverable: bool
