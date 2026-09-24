"""The MCP server's protocol handling, with a stand-in for the SDK client (§26 3.5)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from ai_memory.errors import MemoryAPIError  # noqa: E402
from ai_memory.mcp import TOOLS, MCPServer, serve_stdio  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def query(self, customer_id, question, **kwargs):
        self.calls.append(("query", customer_id, question, kwargs))
        if customer_id == "missing":
            raise MemoryAPIError("Customer 'missing' not found.", status=404, code="not_found")
        memory = SimpleNamespace(id="mem_1", type="problem", content="Shopify sync fails.", score=0.9)
        return SimpleNamespace(answer="Their Shopify sync fails.", confidence=0.8, memories=[memory], run_id="qry_1")

    def check_action(self, customer_id, action, request, **kwargs):
        self.calls.append(("check", customer_id, action, request, kwargs))
        return SimpleNamespace(
            decision="deny", allowed=False, summary="The customer has 1 open problem.", id="chk_1",
            reasons=[{"rule": "open_problem_blocks_selling", "decision": "deny", "explanation": "x"}],
            evidence=["mem_1"], approval=None,
        )


def rpc(server, method, params=None, ident=1, **kwargs):
    message = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message, **kwargs)


def make():
    client = FakeClient()
    return MCPServer(lambda key: client if key != "none" else None), client


def test_initialize_negotiates_a_supported_version():
    server, _ = make()
    reply = rpc(server, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert "check_action" in reply["result"]["instructions"]
    future = rpc(server, "initialize", {"protocolVersion": "2099-01-01"})
    assert future["result"]["protocolVersion"] == "2025-06-18"


def test_every_tool_is_described_with_an_object_schema():
    server, _ = make()
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == {tool.name for tool in TOOLS}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"] and tool["annotations"]["title"]
    writes = {tool["name"] for tool in tools if not tool["annotations"]["readOnlyHint"]}
    assert writes == {"check_action", "remember"}


def test_a_tool_call_returns_text_and_structured_content():
    server, client = make()
    reply = rpc(server, "tools/call", {"name": "ask_memory", "arguments": {"customer_id": "acme", "question": "What is broken?"}})
    result = reply["result"]
    assert result["isError"] is False
    assert "Shopify sync fails" in result["content"][0]["text"]
    assert result["structuredContent"]["run_id"] == "qry_1"
    # Older protocol versions get the text only.
    old = rpc(server, "tools/call", {"name": "ask_memory", "arguments": {"customer_id": "acme", "question": "x?"}}, protocol="2024-11-05")
    assert "structuredContent" not in old["result"]


def test_failures_are_tool_errors_not_protocol_errors():
    server, _ = make()
    missing = rpc(server, "tools/call", {"name": "ask_memory", "arguments": {"customer_id": "missing", "question": "x?"}})
    assert missing["result"]["isError"] is True and "404" in missing["result"]["content"][0]["text"]
    bad = rpc(server, "tools/call", {"name": "ask_memory", "arguments": {"question": "x?"}})
    assert bad["result"]["isError"] is True and "customer_id" in bad["result"]["content"][0]["text"]
    no_key = rpc(server, "tools/call", {"name": "ask_memory", "arguments": {"customer_id": "a", "question": "x?"}}, api_key="none")
    assert no_key["result"]["isError"] is True


def test_check_action_tells_the_model_not_to_proceed():
    server, client = make()
    reply = rpc(server, "tools/call", {"name": "check_action", "arguments": {"customer_id": "acme", "action": "offer_upgrade", "request": {"plan": "pro"}}})
    assert reply["result"]["content"][0]["text"].startswith("DENIED:")
    assert client.calls[-1][3] == {"plan": "pro"}


def test_protocol_errors_and_notifications():
    server, _ = make()
    assert rpc(server, "no/such/method")["error"]["code"] == -32601
    assert rpc(server, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    batch = server.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_stdio_answers_line_by_line():
    server, _ = make()
    lines = "\n".join(
        json.dumps(message)
        for message in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ask_memory", "arguments": {"customer_id": "a", "question": "b?"}}},
        )
    )
    out = io.StringIO()
    serve_stdio(server, stdin=io.StringIO(lines + "\nnot json\n"), stdout=out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [reply.get("id") for reply in replies] == [1, 2, None]
    # The version negotiated at initialize is used for later calls on the same stream.
    assert "structuredContent" not in replies[1]["result"]
    assert replies[2]["error"]["code"] == -32700
