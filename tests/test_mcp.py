"""Tests for the generic MCP client manager."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from victoria.mcp.manager import MCPManager, load_mcp_config, is_write_tool
from victoria.tools.registry import ToolRegistry


def _tool(name, desc="d", schema=None):
    return SimpleNamespace(name=name, description=desc,
                           inputSchema=schema or {"type": "object", "properties": {}})


def _result(text, is_error=False):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=is_error)


# ---------------------------------------------------------------------------
# write-tool heuristic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["create_issue", "send_message", "delete_file",
                                   "update_row", "post_message", "write_note", "run_query"])
def test_is_write_tool_true(name):
    assert is_write_tool(name) is True


@pytest.mark.parametrize("name", ["read_file", "list_directory", "search", "get_weather", "fetch"])
def test_is_write_tool_false(name):
    assert is_write_tool(name) is False


# ---------------------------------------------------------------------------
# config loading
# ---------------------------------------------------------------------------

def test_load_config_parses_servers(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["x"]}}}))
    cfg = load_mcp_config(str(p))
    assert cfg == {"fs": {"command": "npx", "args": ["x"]}}


def test_load_config_absent_returns_empty(tmp_path):
    assert load_mcp_config(str(tmp_path / "nope.json")) == {}


def test_load_config_invalid_returns_empty(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text("{ not json")
    assert load_mcp_config(str(p)) == {}


# ---------------------------------------------------------------------------
# tool selection / guardrails
# ---------------------------------------------------------------------------

def test_select_tools_allowlist():
    tools = [_tool("read_file"), _tool("write_file"), _tool("list_dir")]
    out = MCPManager._select_tools(tools, {"allowedTools": ["read_file", "list_dir"]})
    assert {t.name for t in out} == {"read_file", "list_dir"}


def test_select_tools_readonly_drops_writes():
    tools = [_tool("read_file"), _tool("delete_file"), _tool("search")]
    out = MCPManager._select_tools(tools, {"readOnly": True})
    assert {t.name for t in out} == {"read_file", "search"}


# ---------------------------------------------------------------------------
# registration + call routing
# ---------------------------------------------------------------------------

def test_register_namespaces_into_registry():
    reg = ToolRegistry()
    mgr = MCPManager(registry=reg)
    mgr._register_tool("slack", _tool("read_channel", "reads a channel"))
    assert "mcp__slack__read_channel" in reg
    assert mgr.tool_map["mcp__slack__read_channel"] == ("slack", "read_channel")
    # advertised in the OpenAI tool format
    names = [t["function"]["name"] for t in reg.get_ollama_tools()]
    assert "mcp__slack__read_channel" in names


async def test_registered_tool_executes_via_session():
    reg = ToolRegistry()
    mgr = MCPManager(registry=reg)
    session = SimpleNamespace(call_tool=AsyncMock(return_value=_result("channel contents")))
    mgr.sessions["slack"] = session
    mgr._register_tool("slack", _tool("read_channel"))

    # Execute through the normal registry path (as the tool loop would).
    result = await reg.execute("mcp__slack__read_channel", channel="general")
    assert result == "channel contents"
    session.call_tool.assert_awaited_once_with("read_channel", {"channel": "general"})


async def test_call_unknown_server():
    mgr = MCPManager(registry=ToolRegistry())
    assert "not connected" in await mgr.call("ghost", "x", {})


async def test_call_error_result_is_flagged():
    reg = ToolRegistry()
    mgr = MCPManager(registry=reg)
    mgr.sessions["s"] = SimpleNamespace(call_tool=AsyncMock(return_value=_result("boom", is_error=True)))
    out = await mgr.call("s", "t", {})
    assert out.startswith("Error:") and "boom" in out


async def test_call_exception_is_caught():
    reg = ToolRegistry()
    mgr = MCPManager(registry=reg)
    mgr.sessions["s"] = SimpleNamespace(call_tool=AsyncMock(side_effect=RuntimeError("nope")))
    out = await mgr.call("s", "t", {})
    assert "MCP tool error" in out and "nope" in out


async def test_aclose_unregisters_tools():
    reg = ToolRegistry()
    mgr = MCPManager(registry=reg)
    mgr.sessions["s"] = SimpleNamespace(call_tool=AsyncMock(return_value=_result("x")))
    mgr._register_tool("s", _tool("read_thing"))
    assert "mcp__s__read_thing" in reg
    await mgr.aclose()
    assert "mcp__s__read_thing" not in reg
    assert mgr.tool_map == {}
