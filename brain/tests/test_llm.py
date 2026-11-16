import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from minegpt_brain.agent.__main__ import main
from minegpt_brain.llm import BackendError, ToolResult, ToolSpec, make_backend
from minegpt_brain.llm.claude import ClaudeBackend
from minegpt_brain.llm.local import LocalBackend

TOOLS = [ToolSpec("mine", "Mines blocks.", {"type": "object", "properties": {"block": {"type": "string"}}})]


class Block(SimpleNamespace):
    def model_dump(self, mode=None, exclude_none=False):
        return dict(vars(self))


def response(*content, stop="tool_use", model="claude-opus-5-5"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80,
                            cache_creation_input_tokens=None)
    return SimpleNamespace(content=list(content), stop_reason=stop, usage=usage, model=model)


class FakeClient:
    def __init__(self, *responses, error=None):
        self.responses = list(responses)
        self.requests = []
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params):
        if self.error is not None:
            raise self.error
        self.requests.append({**params, "messages": list(params["messages"])})
        return self.responses.pop(0)


def test_claude_requests_use_the_recommended_settings():
    thinking = Block(type="thinking", thinking="logs first", signature="abc")
    text = Block(type="text", text="Getting wood.")
    call = Block(type="tool_use", id="toolu_1", name="mine", input={"block": "#minecraft:logs"})
    client = FakeClient(response(thinking, text, call),
                        response(Block(type="text", text="Done."), stop="end_turn"))
    conversation = ClaudeBackend(client=client).start("You play Minecraft.", TOOLS)
    conversation.add_user("Goal: get wood")
    turn = conversation.next()

    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert request["output_config"] == {"effort": "medium"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["betas"] == ["server-side-fallback-2026-07-01"] and request["fallbacks"] == "default"
    assert request["tools"] == [{"name": "mine", "description": "Mines blocks.", "input_schema": TOOLS[0].parameters}]
    assert "temperature" not in request and "tool_choice" not in request
    assert turn.text == "Getting wood." and turn.stop == "tool_use"
    assert [(c.id, c.name, c.args) for c in turn.tool_calls] == [("toolu_1", "mine", {"block": "#minecraft:logs"})]
    assert turn.usage == {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80,
                          "cache_creation_input_tokens": 0}
    assert "signature" not in turn.raw[0]

    conversation.add_results([ToolResult("toolu_1", "failed: no logs", is_error=True)], note="Health 20/20")
    conversation.next()
    messages = client.requests[1]["messages"]
    # The assistant turn goes back exactly as returned, thinking included.
    assert messages[1] == {"role": "assistant", "content": [thinking, text, call]}
    assert messages[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "failed: no logs", "is_error": True},
        {"type": "text", "text": "Health 20/20"}]}


def test_claude_drops_blocks_from_before_a_fallback():
    early = Block(type="tool_use", id="toolu_early", name="mine", input={})
    late = Block(type="tool_use", id="toolu_late", name="mine", input={"block": "stone"})
    client = FakeClient(response(Block(type="thinking", thinking="", signature="s"), early,
                                 Block(type="fallback"), Block(type="text", text="ok"), late,
                                 model="claude-opus-4-8"))
    conversation = ClaudeBackend(client=client).start("system", TOOLS)
    conversation.add_user("go")
    turn = conversation.next()
    assert [c.id for c in turn.tool_calls] == ["toolu_late"]
    assert [b.type for b in conversation.messages[-1]["content"]] == ["fallback", "text", "tool_use"]
    assert turn.model == "claude-opus-4-8"


def test_claude_fallbacks_only_where_supported():
    client = FakeClient(response(Block(type="text", text="hi"), stop="end_turn"))
    conversation = ClaudeBackend(model="claude-haiku-5-5", effort="low", client=client).start("system", TOOLS)
    conversation.add_user("hello")
    conversation.next()
    assert "betas" not in client.requests[0] and "fallbacks" not in client.requests[0]
    assert client.requests[0]["output_config"] == {"effort": "low"}


def test_claude_errors_explain_the_key():
    class AuthenticationError(Exception):
        pass

    conversation = ClaudeBackend(client=FakeClient(error=AuthenticationError("401"))).start("system", TOOLS)
    conversation.add_user("hello")
    with pytest.raises(BackendError, match="ANTHROPIC_API_KEY"):
        conversation.next()


class FakeServer(BaseHTTPRequestHandler):
    replies: list = []
    bodies: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeServer.bodies.append({"path": self.path, "beta_header": self.headers.get("anthropic-beta"), **body})
        reply = json.dumps(FakeServer.replies.pop(0)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    FakeServer.replies, FakeServer.bodies = [], []
    httpd = HTTPServer(("127.0.0.1", 0), FakeServer)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def test_local_backend_speaks_chat_completions(server):
    FakeServer.replies = [
        {"model": "qwen3:14b", "usage": {"prompt_tokens": 50, "completion_tokens": 9}, "choices": [{
            "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "mine", "arguments": "{\"block\": \"stone\"}"}}]},
        }]},
        {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "All done."}}]},
    ]
    backend = LocalBackend("qwen3:14b", base_url=f"http://127.0.0.1:{server.server_port}/v1")
    conversation = backend.start("You play Minecraft.", TOOLS)
    conversation.add_user("Goal: stone")
    turn = conversation.next()
    assert turn.stop == "tool_use" and turn.tool_calls[0].args == {"block": "stone"}
    assert turn.usage == {"input_tokens": 50, "output_tokens": 9}

    conversation.add_results([ToolResult("c1", "no stone", is_error=True)], note="Health 20/20")
    turn = conversation.next()
    assert turn.text == "All done." and turn.stop == "end_turn" and not turn.tool_calls

    first, second = FakeServer.bodies
    assert first["path"] == "/v1/chat/completions" and first["model"] == "qwen3:14b"
    assert first["tools"][0] == {"type": "function", "function": {
        "name": "mine", "description": "Mines blocks.", "parameters": TOOLS[0].parameters}}
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "tool", "user"]
    assert second["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "Error: no stone"}


def test_local_backend_reports_an_unreachable_server():
    conversation = LocalBackend("m", base_url="http://127.0.0.1:1/v1").start("system", TOOLS)
    conversation.add_user("hi")
    with pytest.raises(BackendError, match="Is the model server running"):
        conversation.next()


def test_local_backend_needs_a_model(capsys):
    with pytest.raises(BackendError, match="--model"):
        make_backend("local")
    assert main(["--backend", "local"]) == 2
    assert "--model" in capsys.readouterr().err


def test_claude_backend_with_the_real_sdk(server):
    """Runs the request through the anthropic package against a fake API, if it is installed."""
    anthropic = pytest.importorskip("anthropic")
    message = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
               "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
               "content": [{"type": "thinking", "thinking": "stone first", "signature": "sig"},
                           {"type": "tool_use", "id": "toolu_1", "name": "mine", "input": {"block": "stone"}}]}
    done = {**message, "id": "msg_2", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]}
    FakeServer.replies = [message, done]
    client = anthropic.Anthropic(api_key="test-key", base_url=f"http://127.0.0.1:{server.server_port}",
                                 max_retries=0)
    conversation = ClaudeBackend(client=client).start("You play Minecraft.", TOOLS)
    conversation.add_user("Goal: stone")
    turn = conversation.next()
    assert turn.tool_calls[0].args == {"block": "stone"}
    conversation.add_results([ToolResult("toolu_1", "ok")])
    assert conversation.next().text == "Done."

    first, second = FakeServer.bodies
    assert first["path"].startswith("/v1/messages")
    assert first["beta_header"] == "server-side-fallback-2026-07-01" and "betas" not in first
    assert first["fallbacks"] == "default" and first["cache_control"] == {"type": "ephemeral"}
    assert first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert second["messages"][1]["content"][0] == {"type": "thinking", "thinking": "stone first", "signature": "sig"}
    assert second["messages"][2]["content"][0]["tool_use_id"] == "toolu_1"
