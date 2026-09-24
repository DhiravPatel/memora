"""An MCP server for Memora (§26 3.5): persistent customer memory for any MCP agent.

    MEMORA_API_KEY=mk_live_… ai-memory-mcp                      # stdio, for a local agent
    ai-memory-mcp --transport http --port 8765                   # Streamable HTTP, per-caller keys

Each tool is a call through the Python SDK, so the server can do exactly what its API key
can — the key's scopes, clearance and agent profile apply unchanged.
"""

from ai_memory.mcp.server import SUPPORTED_VERSIONS, MCPServer
from ai_memory.mcp.tools import TOOLS, Tool, ToolResult
from ai_memory.mcp.transports import ClientCache, make_http_server, serve_stdio

__all__ = [
    "SUPPORTED_VERSIONS",
    "TOOLS",
    "ClientCache",
    "MCPServer",
    "Tool",
    "ToolResult",
    "make_http_server",
    "serve_stdio",
]
