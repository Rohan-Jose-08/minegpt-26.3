"""The Claude API backend, through the official ``anthropic`` package.

Install it with ``pip install -e ".[agent]"``. The key comes from the ``ANTHROPIC_API_KEY``
environment variable (or a profile saved by ``ant auth login``); it is never stored here.

Requests use adaptive thinking, an explicit effort level and prompt caching. The system
prompt and tool list never change during a conversation, so after the first request most of
the input is read from the cache. For models that support it, refusal fallbacks are on: if a
request is declined, the API retries it on Anthropic's recommended fallback model within the
same call.
"""

from __future__ import annotations

import base64
from typing import Any, Optional, Sequence

from . import BackendError, ToolCall, ToolResult, ToolSpec, Turn

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 16000

#: Models that accept ``fallbacks: "default"``.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeBackend:
    #: Claude reads images, so tools may return pictures of the game.
    vision = True

    def __init__(self, model: Optional[str] = None, effort: Optional[str] = None, fallbacks: bool = True,
                 client: Any = None):
        self.model = model or DEFAULT_MODEL
        self.effort = effort or DEFAULT_EFFORT
        self.fallbacks = fallbacks and self.model in _FALLBACK_MODELS
        self.name = f"claude:{self.model}"
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise BackendError('the claude backend needs the anthropic package: pip install -e ".[agent]"') from e
            client = anthropic.Anthropic(max_retries=5)
        self._client = client

    def start(self, system: str, tools: Sequence[ToolSpec]) -> "ClaudeConversation":
        return ClaudeConversation(self, system, [
            {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
        ])


class ClaudeConversation:
    def __init__(self, backend: ClaudeBackend, system: str, tools: list[dict[str, Any]]):
        self._backend = backend
        self._system = system
        self._tools = tools
        self.messages: list[dict[str, Any]] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_results(self, results: Sequence[ToolResult], note: Optional[str] = None) -> None:
        content: list[dict[str, Any]] = []
        for result in results:
            block: dict[str, Any] = {"type": "tool_result", "tool_use_id": result.call_id, "content": result.content}
            if result.images:
                # Pictures ride inside the tool result, each after its caption.
                parts: list[dict[str, Any]] = [{"type": "text", "text": result.content}]
                for image in result.images:
                    if image.caption:
                        parts.append({"type": "text", "text": image.caption})
                    parts.append({"type": "image", "source": {
                        "type": "base64", "media_type": f"image/{image.format}",
                        "data": base64.b64encode(image.data).decode("ascii")}})
                block["content"] = parts
            if result.is_error:
                block["is_error"] = True
            content.append(block)
        if note:
            content.append({"type": "text", "text": note})
        if content:
            self.messages.append({"role": "user", "content": content})

    def next(self) -> Turn:
        backend = self._backend
        params: dict[str, Any] = {
            "model": backend.model,
            "max_tokens": MAX_TOKENS,
            "system": self._system,
            "tools": self._tools,
            "messages": self.messages,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": backend.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if backend.fallbacks:
            params["betas"] = [_FALLBACK_BETA]
            params["fallbacks"] = "default"
        try:
            response = backend._client.beta.messages.create(**params)
        except Exception as e:  # the SDK's errors all derive from anthropic.APIError or AnthropicError
            raise BackendError(_describe(e)) from e

        content = _echoable(response.content)
        # The history stays append-only, with thinking blocks as returned, so the cache keeps hitting.
        self.messages.append({"role": "assistant", "content": content})
        text = "".join(block.text for block in content if block.type == "text")
        calls = [ToolCall(block.id, block.name, dict(block.input or {})) for block in content
                 if block.type == "tool_use"]
        usage = response.usage
        return Turn(
            text=text,
            tool_calls=calls,
            stop=response.stop_reason or "end_turn",
            usage={key: getattr(usage, key, None) or 0 for key in (
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")},
            model=getattr(response, "model", backend.model),
            raw=[_loggable(block) for block in content],
        )


def _echoable(blocks: Sequence[Any]) -> list[Any]:
    """The reply's blocks as they may be sent back.

    After a fallback, thinking and tool calls from before the last switch belong to the model
    that declined and must not be echoed; everything else goes back unchanged.
    """
    switches = [i for i, block in enumerate(blocks) if block.type == "fallback"]
    if not switches:
        return list(blocks)
    last = switches[-1]
    return [block for i, block in enumerate(blocks)
            if i >= last or block.type not in ("thinking", "redacted_thinking", "tool_use")]


def _loggable(block: Any) -> Any:
    data = block.model_dump(mode="json", exclude_none=True) if hasattr(block, "model_dump") else dict(block)
    data.pop("signature", None)
    return data


def _describe(error: Exception) -> str:
    name = type(error).__name__
    if name == "AuthenticationError" or "authentication" in str(error).lower():
        return "the Claude API rejected the credentials: set ANTHROPIC_API_KEY (or run ant auth login)"
    return f"Claude API request failed: {name}: {error}"
