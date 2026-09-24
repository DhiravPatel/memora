"""``ai-memory-mcp`` / ``python -m ai_memory.mcp``."""

from __future__ import annotations

import argparse
import os
import sys

from ai_memory import __version__
from ai_memory.client import MemoryClient
from ai_memory.mcp.server import MCPServer
from ai_memory.mcp.transports import ClientCache, make_http_server, serve_stdio


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-memory-mcp", description="Memora as an MCP server.")
    parser.add_argument("--transport", choices=("stdio", "http"), default=os.environ.get("MEMORA_MCP_TRANSPORT", "stdio"))
    parser.add_argument("--base-url", default=os.environ.get("MEMORA_BASE_URL"), help="Memora API URL (MEMORA_BASE_URL).")
    parser.add_argument("--api-key", default=os.environ.get("MEMORA_API_KEY"), help="API key (MEMORA_API_KEY). Required for stdio; the default for HTTP callers that send none.")
    parser.add_argument("--agent-name", default=os.environ.get("MEMORA_AGENT_NAME"), help="Label for runs and checks made by an unbound key.")
    parser.add_argument("--host", default=os.environ.get("MEMORA_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MEMORA_MCP_PORT", "8765")))
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--allowed-origin", action="append", default=[], help="Browser origins allowed to call the HTTP server.")
    args = parser.parse_args(argv)

    def client_for(api_key: str) -> MemoryClient:
        return MemoryClient(api_key=api_key, base_url=args.base_url, agent_name=args.agent_name)

    if args.transport == "stdio":
        if not args.api_key:
            print("ai-memory-mcp: set MEMORA_API_KEY (or pass --api-key).", file=sys.stderr)
            return 2
        client = client_for(args.api_key)
        serve_stdio(MCPServer(lambda _key: client, version=__version__))
        return 0

    server = make_http_server(
        MCPServer(ClientCache(client_for), version=__version__),
        host=args.host,
        port=args.port,
        path=args.path,
        default_api_key=args.api_key,
        allowed_origins=tuple(args.allowed_origin),
    )
    print(f"ai-memory-mcp: listening on http://{args.host}:{args.port}{args.path}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
