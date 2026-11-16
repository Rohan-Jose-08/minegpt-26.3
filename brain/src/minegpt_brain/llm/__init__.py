"""Language model backends for the agent, behind one small interface.

A backend starts conversations. A conversation keeps its own message history in whatever
form its API wants, so the agent only deals with text, tool calls and tool results:

    conversation = backend.start(system_prompt, tools)
    conversation.add_user("Goal: full iron armor ...")
    turn = conversation.next()
    for call in turn.tool_calls:
        ...
    conversation.add_results([ToolResult(call.id, "ok", False) ...], note="Health 20/20 ...")

Two backends ship: ``claude`` (the Claude API, through the official ``anthropic`` package)
and ``local`` (any server with an OpenAI-style ``/v1/chat/completions`` endpoint, such as
Ollama, llama.cpp or vLLM, for running open models on your own machine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence


@dataclass
class ToolSpec:
    """A tool the model may call. ``parameters`` is a JSON schema for the arguments object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Image:
    """A picture to show the model: the file's bytes and its type, ``jpeg`` or ``png``."""

    data: bytes
    format: str = "jpeg"
    #: What the picture shows, in a few words; sent as text just before it.
    caption: str = ""


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False
    #: Pictures that go with the result. Only backends with ``vision`` show them to the model.
    images: list[Image] = field(default_factory=list)


@dataclass
class Turn:
    """One reply from the model."""

    text: str
    tool_calls: list[ToolCall]
    #: Why the model stopped: ``tool_use``, ``end_turn``, ``max_tokens`` or ``refusal``.
    stop: str
    #: Token counts for this request, as the backend reports them.
    usage: dict[str, int] = field(default_factory=dict)
    #: The model that produced the reply, which can differ from the one asked for.
    model: str = ""
    #: The reply as the API returned it, as JSON-ready data for episode logs.
    raw: Any = None


class Conversation(Protocol):
    def add_user(self, text: str) -> None:
        """Adds a user message."""

    def add_results(self, results: Sequence[ToolResult], note: Optional[str] = None) -> None:
        """Answers every tool call of the last turn, with an optional text note after them."""

    def next(self) -> Turn:
        """Asks the model for its next turn and adds the reply to the history."""


class Backend(Protocol):
    #: Short description for logs, such as ``claude:claude-opus-5-5``.
    name: str
    #: True if the model can be shown pictures (``ToolResult.images``).
    vision: bool

    def start(self, system: str, tools: Sequence[ToolSpec]) -> Conversation:
        """Starts an empty conversation."""


class BackendError(RuntimeError):
    """The model could not be reached, or answered in a way the agent cannot use."""


def make_backend(kind: str, model: Optional[str] = None, *, effort: Optional[str] = None,
                 base_url: Optional[str] = None, fallbacks: bool = True,
                 context_chars: Optional[int] = None) -> Backend:
    """Builds a backend by name: ``claude`` or ``local``."""
    if kind == "claude":
        from .claude import ClaudeBackend

        return ClaudeBackend(model=model, effort=effort, fallbacks=fallbacks)
    if kind == "local":
        from .local import LocalBackend

        if not model:
            raise BackendError("the local backend needs a model name, for example --model qwen3:14b")
        return LocalBackend(model=model, base_url=base_url, context_chars=context_chars)
    raise BackendError(f"unknown backend {kind!r}; use claude or local")
