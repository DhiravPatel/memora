"""stdio and Streamable HTTP, the two MCP transports — standard library only.

**stdio** is for a local agent (a desktop assistant, an IDE): one process per user, one
API key from the environment, newline-delimited JSON on stdin/stdout, logs on stderr.

**Streamable HTTP** is for a hosted agent: ``POST /mcp`` with one JSON-RPC message (or a
batch) and the caller's own API key as a Bearer token. Replies are plain JSON — which the
specification allows a server to choose — and the server keeps no session, so it can sit
behind any load balancer. ``GET /mcp`` answers 405: there is nothing to stream.
"""

from __future__ import annotations

import json
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, TextIO
from urllib.parse import urlparse

from ai_memory.mcp.server import INVALID_REQUEST, PARSE_ERROR, MCPServer

MAX_BODY_BYTES = 1_000_000


def serve_stdio(server: MCPServer, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Read messages until stdin closes. One line in, at most one line out."""
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    protocol: str | None = None
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply: Any = {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "Invalid JSON."}}
        else:
            if isinstance(message, dict) and message.get("method") == "initialize":
                protocol = server.negotiate((message.get("params") or {}).get("protocolVersion"))
            reply = server.handle(message, protocol=protocol)
        if reply is not None:
            sink.write(json.dumps(reply, separators=(",", ":"), default=str) + "\n")
            sink.flush()


class ClientCache:
    """One SDK client per API key, most recently used kept — an HTTP server's callers each
    act as their own key, so their own project, scopes and agent profile."""

    def __init__(self, factory: Callable[[str], Any], size: int = 64) -> None:
        self.factory = factory
        self.size = size
        self._clients: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, api_key: str | None) -> Any:
        if not api_key:
            return None
        with self._lock:
            client = self._clients.pop(api_key, None) or self.factory(api_key)
            self._clients[api_key] = client
            while len(self._clients) > self.size:
                _, evicted = self._clients.popitem(last=False)
                close = getattr(evicted, "close", None)
                if close:
                    close()
            return client


def _local(origin: str) -> bool:
    host = urlparse(origin).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def make_http_server(
    server: MCPServer,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    path: str = "/mcp",
    default_api_key: str | None = None,
    allowed_origins: tuple[str, ...] = (),
) -> ThreadingHTTPServer:
    """Build (without starting) the HTTP transport. ``serve_forever()`` runs it."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "memora-mcp"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            sys.stderr.write("memora-mcp http: " + (format % args) + "\n")

        def _send(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
            payload = b"" if body is None else json.dumps(body, default=str).encode()
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def _origin_ok(self) -> bool:
            # Browsers send Origin; a page on another site must not drive a local server
            # (DNS rebinding). Non-browser clients send none and are judged by their key.
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return origin in allowed_origins or (not allowed_origins and _local(origin))

        def _api_key(self) -> str | None:
            authorization = self.headers.get("Authorization") or ""
            if authorization.lower().startswith("bearer "):
                return authorization.split(" ", 1)[1].strip() or None
            return self.headers.get("X-API-Key") or default_api_key

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if self.path.rstrip("/") == "/health":
                self._send(200, {"status": "ok"})
            elif urlparse(self.path).path == path:
                self._send(405, {"error": "This server does not stream; POST messages to it."}, {"Allow": "POST"})
            else:
                self._send(404, {"error": "Not found."})

        def do_DELETE(self) -> None:  # noqa: N802
            self._send(405, {"error": "There are no sessions to end."}, {"Allow": "POST"})

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != path:
                self._send(404, {"error": "Not found."})
                return
            if not self._origin_ok():
                self._send(403, {"error": "Origin not allowed."})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send(413 if length > MAX_BODY_BYTES else 400, {"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "Send one JSON-RPC message as the body."}})
                return
            try:
                message = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "Invalid JSON."}})
                return
            api_key = self._api_key()
            wants_tools = any(
                isinstance(item, dict) and item.get("method") == "tools/call"
                for item in (message if isinstance(message, list) else [message])
            )
            if wants_tools and not api_key:
                self._send(401, {"error": "Send your Memora API key as a Bearer token."}, {"WWW-Authenticate": "Bearer"})
                return
            protocol = self.headers.get("MCP-Protocol-Version")
            if isinstance(message, dict) and message.get("method") == "initialize":
                protocol = server.negotiate((message.get("params") or {}).get("protocolVersion"))
            reply = server.handle(message, api_key=api_key, protocol=protocol)
            if reply is None:
                self._send(202)
            else:
                self._send(200, reply)

    return ThreadingHTTPServer((host, port), Handler)
