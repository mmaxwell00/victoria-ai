"""A generic MCP (Model Context Protocol) client for Victoria.

Reads a Claude-Desktop-compatible config file and connects to each configured
server (stdio subprocess or remote SSE). Each server's tools are registered into
Victoria's tool registry under a namespaced name — `mcp__<server>__<tool>` — so
they flow through the normal tool-calling loop and can't collide with built-ins.

Guardrails:
- `disabled: true`       — skip a server entirely.
- `allowedTools: [...]`  — expose only these tools from a server.
- `readOnly: true`       — drop write-ish tools (create/update/delete/send/...).
Every MCP tool call is logged. Imported servers run under YOUR credentials, so
only configure servers you trust.
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

from victoria.config import settings
from victoria.tools.registry import registry as global_registry

logger = logging.getLogger(__name__)

# Tool-name verbs that imply a mutation — filtered out when readOnly is set.
_WRITE_VERBS = (
    "create", "update", "delete", "remove", "write", "send", "post", "put",
    "patch", "set", "append", "move", "rename", "upload", "insert", "add",
    "edit", "execute", "run", "publish", "merge", "close", "archive", "drop",
)


def is_write_tool(name: str) -> bool:
    n = name.lower()
    return any(re.search(rf"(^|[_\-]){v}([_\-]|$)", n) or n.startswith(v) for v in _WRITE_VERBS)


def load_mcp_config(path: Optional[str] = None) -> dict:
    """Load the MCP server config. Returns {} if the file is absent/invalid."""
    cfg_path = Path(path or settings.mcp_config_path)
    if not cfg_path.exists():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to parse MCP config %s", cfg_path)
        return {}
    return data.get("mcpServers", data) or {}


class MCPManager:
    """Connects to MCP servers and registers their tools into the tool registry."""

    def __init__(self, registry=None):
        # NB: ToolRegistry defines __len__, so an empty one is falsy — must use
        # an explicit None check, not `registry or global_registry`.
        self.registry = registry if registry is not None else global_registry
        self._stack: Optional[AsyncExitStack] = None
        self.sessions: dict = {}          # server -> ClientSession
        self.tool_map: dict = {}          # namespaced tool name -> (server, real_tool)
        self.errors: dict = {}            # server -> error string

    @staticmethod
    def _select_tools(tools: list, conf: dict) -> list:
        allowed = conf.get("allowedTools")
        read_only = conf.get("readOnly", False)
        out = []
        for t in tools:
            if allowed is not None and t.name not in allowed:
                continue
            if read_only and is_write_tool(t.name):
                continue
            out.append(t)
        return out

    async def _connect_one(self, name: str, conf: dict) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if conf.get("url"):
            from mcp.client.sse import sse_client
            read, write = await self._stack.enter_async_context(
                sse_client(conf["url"], headers=conf.get("headers"))
            )
        else:
            params = StdioServerParameters(
                command=conf["command"],
                args=conf.get("args", []),
                env=conf.get("env") or None,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))

        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self.sessions[name] = session

        listed = (await session.list_tools()).tools
        selected = self._select_tools(listed, conf)
        for tool in selected:
            self._register_tool(name, tool)
        logger.info("MCP '%s' connected — %d/%d tools exposed", name, len(selected), len(listed))

    def _register_tool(self, server: str, tool) -> None:
        namespaced = f"mcp__{server}__{tool.name}"
        self.tool_map[namespaced] = (server, tool.name)
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        desc = (tool.description or f"{tool.name} (from {server})")[:1024]

        async def _call(__server=server, __tool=tool.name, **kwargs):
            return await self.call(__server, __tool, kwargs)

        self.registry.add(namespaced, desc, schema, _call)

    async def call(self, server: str, tool: str, arguments: dict) -> str:
        session = self.sessions.get(server)
        if session is None:
            return f"MCP server '{server}' is not connected."
        logger.info("MCP call: %s.%s(%s)", server, tool, ", ".join(arguments or {}))
        try:
            result = await session.call_tool(tool, arguments or {})
        except Exception as exc:
            logger.exception("MCP call failed: %s.%s", server, tool)
            return f"MCP tool error ({server}.{tool}): {exc}"
        return self._result_text(result)

    @staticmethod
    def _result_text(result) -> str:
        parts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(getattr(block, "data", block)))
        text = "\n".join(parts).strip() or "(no output)"
        return f"Error: {text}" if getattr(result, "isError", False) else text

    async def connect_all(self, config: Optional[dict] = None) -> None:
        """Connect every enabled server. A failing server is logged and skipped."""
        servers = config if config is not None else load_mcp_config()
        if not servers:
            logger.info("No MCP servers configured (%s absent) — MCP off.", settings.mcp_config_path)
            return
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        for name, conf in servers.items():
            if conf.get("disabled"):
                continue
            try:
                await self._connect_one(name, conf)
            except Exception as exc:
                self.errors[name] = str(exc)
                logger.exception("MCP server '%s' failed to connect — skipping", name)

    async def aclose(self) -> None:
        # Unregister tools, then tear down all sessions/transports.
        for namespaced in list(self.tool_map):
            self.registry.remove(namespaced)
        self.tool_map.clear()
        self.sessions.clear()
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                logger.exception("Error closing MCP sessions")
            self._stack = None

    def status(self) -> dict:
        return {
            "servers": sorted(self.sessions),
            "tools": len(self.tool_map),
            "errors": self.errors,
        }


# Module-level singleton connected at app startup.
mcp_manager = MCPManager()
