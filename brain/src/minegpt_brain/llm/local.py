"""A backend for open models served on your own machine.

Talks to any server with an OpenAI-style ``/v1/chat/completions`` endpoint and tool calling:
Ollama (the default address), llama.cpp's ``llama-server``, vLLM, LM Studio and others. This is
how a MineGPT model trained in Phase 6 will plug in. Only the standard library is used.

Set ``MINEGPT_LOCAL_API_KEY`` if the server wants a bearer token. Long histories are cut to
fit a small context window with :func:`.history.fit_history` (``context_chars``; 0 sends all).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional, Sequence

from . import BackendError, ToolCall, ToolResult, ToolSpec, Turn
from .history import DEFAULT_BUDGET, fit_history

DEFAULT_BASE_URL = "http://localhost:11434/v1"
_STOPS = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


class LocalBackend:
    #: Local models here are text-only: pictures in tool results are left out.
    vision = False

    def __init__(self, model: str, base_url: Optional[str] = None, timeout: float = 600.0,
                 context_chars: Optional[int] = None):
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.context_chars = DEFAULT_BUDGET if context_chars is None else context_chars
        self.name = f"local:{model}"

    def start(self, system: str, tools: Sequence[ToolSpec]) -> "LocalConversation":
        return LocalConversation(self, system, [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ])

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("MINEGPT_LOCAL_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise BackendError(f"{self.base_url} answered {e.code}: {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise BackendError(f"could not reach {self.base_url}: {e}. Is the model server running?") from e


class LocalConversation:
    def __init__(self, backend: LocalBackend, system: str, tools: list[dict[str, Any]]):
        self._backend = backend
        self._tools = tools
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_results(self, results: Sequence[ToolResult], note: Optional[str] = None) -> None:
        for result in results:
            content = f"Error: {result.content}" if result.is_error else result.content
            self.messages.append({"role": "tool", "tool_call_id": result.call_id, "content": content})
        if note:
            self.add_user(note)

    def next(self) -> Turn:
        messages = fit_history(self.messages, self._backend.context_chars)
        reply = self._backend.post({"model": self._backend.model, "messages": messages,
                                    "tools": self._tools, "tool_choice": "auto"})
        try:
            choice = reply["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise BackendError(f"unexpected reply from {self._backend.base_url}: {str(reply)[:300]}") from e

        calls = []
        tool_calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function", {})
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    arguments = {"_unparsed": arguments}
            call_id = raw.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            calls.append(ToolCall(call_id, function.get("name", ""), arguments if isinstance(arguments, dict) else {}))
            tool_calls.append({"id": call_id, "type": "function",
                               "function": {"name": function.get("name", ""), "arguments": json.dumps(arguments)}})

        assistant: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        self.messages.append(assistant)

        usage = reply.get("usage") or {}
        stop = _STOPS.get(choice.get("finish_reason") or "", "end_turn")
        if calls and stop == "end_turn":
            stop = "tool_use"
        return Turn(
            text=message.get("content") or "",
            tool_calls=calls,
            stop=stop,
            usage={"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
            model=reply.get("model", self._backend.model),
            raw=assistant,
        )
