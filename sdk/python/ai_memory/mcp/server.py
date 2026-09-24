"""Model Context Protocol: JSON-RPC dispatch, independent of transport (§26 3.5).

Implements the parts of MCP a tool server needs — ``initialize``, ``ping``, ``tools/list``
and ``tools/call`` — for protocol versions 2024-11-05 through 2025-06-18. Nothing here
talks to a model and nothing here holds state between requests beyond the client for each
API key, so the same dispatcher serves stdio (one user, one key) and HTTP (many callers,
each with their own key — and therefore their own project, scopes and agent profile).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from ai_memory.errors import MemoryAPIError, MemoryError
from ai_memory.mcp.tools import TOOLS, TOOLS_BY_NAME, ToolInputError, ToolResult

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST = SUPPORTED_VERSIONS[0]
SERVER_NAME = "memora"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "Memora is persistent memory about this product's customers. Before replying to a customer, "
    "call customer_360 or customer_brief. For a specific question, call ask_memory. Before taking "
    "any action that affects a customer — selling, contacting, discounting, refunding, closing a "
    "ticket, changing a plan — call check_action and never proceed when it says DENIED or NEEDS "
    "APPROVAL. Record anything new and durable you learn with remember."
)


def _error(ident: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": error}


def _result(ident: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


class MCPServer:
    """Dispatches MCP messages to tools that call Memora through ``client``.

    ``client_for`` returns the SDK client to use for a caller — for stdio a fixed client,
    for HTTP one per API key — or ``None`` when the caller brought no credentials.
    """

    def __init__(self, client_for: Callable[[str | None], Any], *, version: str = "0.1.0") -> None:
        self.client_for = client_for
        self.version = version

    def handle(self, message: Any, *, api_key: str | None = None, protocol: str | None = None) -> Any:
        """One JSON-RPC message, or a batch. Returns the response, or ``None`` for
        notifications and responses, which need no answer.

        ``protocol`` is the version negotiated for this caller — the transport keeps it,
        because an HTTP server answers many callers and must not remember one of them.
        """
        version = protocol if protocol in SUPPORTED_VERSIONS else LATEST
        if isinstance(message, list):
            if not message:
                return _error(None, INVALID_REQUEST, "An empty batch is not a request.")
            replies = [reply for item in message if (reply := self._one(item, api_key, version)) is not None]
            return replies or None
        return self._one(message, api_key, version)

    def _one(self, message: Any, api_key: str | None, version: str) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Expected a JSON-RPC 2.0 message.")
        method = message.get("method")
        ident = message.get("id")
        if method is None:
            return None  # a response to something we sent; we send nothing that expects one
        is_notification = "id" not in message
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else _error(ident, INVALID_PARAMS, "params must be an object.")

        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [tool.describe() for tool in TOOLS]}
            elif method == "tools/call":
                result = self._call(params, api_key, version)
            elif method in ("resources/list", "resources/templates/list"):
                result = {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            elif method.startswith("notifications/"):
                return None
            else:
                return None if is_notification else _error(ident, METHOD_NOT_FOUND, f"Unknown method: {method}")
        except _ProtocolError as exc:
            return None if is_notification else _error(ident, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 - a bug must not kill the connection
            print(f"memora-mcp: {method} failed: {exc!r}", file=sys.stderr)
            return None if is_notification else _error(ident, INTERNAL_ERROR, "Internal error.")
        return None if is_notification else _result(ident, result)

    @staticmethod
    def negotiate(requested: Any) -> str:
        return requested if requested in SUPPORTED_VERSIONS else LATEST

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": self.negotiate(params.get("protocolVersion")),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Memora customer memory", "version": self.version},
            "instructions": INSTRUCTIONS,
        }

    def _call(self, params: dict[str, Any], api_key: str | None, version: str) -> dict[str, Any]:
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(str(name))
        if tool is None:
            raise _ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _ProtocolError(INVALID_PARAMS, "arguments must be an object.")
        client = self.client_for(api_key)
        if client is None:
            return _tool_result(ToolResult("No Memora API key: send one as a Bearer token or X-API-Key.", is_error=True), version)
        try:
            outcome = tool.handler(client, arguments)
        except ToolInputError as exc:
            outcome = ToolResult(str(exc), is_error=True)
        except MemoryAPIError as exc:
            outcome = ToolResult(f"Memora refused ({exc.status} {exc.code}): {exc.args[0] if exc.args else exc}", is_error=True)
        except MemoryError as exc:
            outcome = ToolResult(f"Memora could not be reached: {exc}", is_error=True)
        except OSError as exc:  # httpx transport errors and friends
            outcome = ToolResult(f"Memora could not be reached: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
            outcome = ToolResult(f"The {tool.name} tool failed: {type(exc).__name__}: {exc}", is_error=True)
        return _tool_result(outcome, version)


class _ProtocolError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _tool_result(outcome: ToolResult, version: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": outcome.text}],
        "isError": outcome.is_error,
    }
    # Structured content arrived in 2025-06-18; older clients get the text alone.
    if outcome.data and version >= "2025-06-18":
        result["structuredContent"] = json.loads(json.dumps(outcome.data, default=str))
    return result
