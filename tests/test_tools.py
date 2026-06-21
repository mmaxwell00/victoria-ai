"""Unit tests for Victoria tools — no real network calls."""
import pytest
from victoria.tools import load_all_tools
from victoria.tools.registry import registry
from victoria.tools.calculator import calculate
from victoria.tools.datetime_tool import get_datetime


# Ensure all tools are loaded once for the whole test session.
load_all_tools()


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_registry_registers_tool():
    """After load_all_tools(), the registry should contain exactly 4 tools."""
    assert len(registry) == 4


async def test_registry_execute_unknown_tool():
    """Executing an unknown tool name returns an error string without raising."""
    result = await registry.execute("does_not_exist")
    assert "Unknown tool" in result
    assert "does_not_exist" in result


# ---------------------------------------------------------------------------
# Calculator tests
# ---------------------------------------------------------------------------

def test_calculate_basic():
    assert calculate("2 + 2") == "2 + 2 = 4"


def test_calculate_complex():
    result = calculate("(10 + 5) * 2")
    assert result == "(10 + 5) * 2 = 30"


def test_calculate_power():
    assert calculate("2 ** 8") == "2 ** 8 = 256"


def test_calculate_invalid():
    result = calculate("import os")
    assert result.startswith("Calculation error:")


# ---------------------------------------------------------------------------
# Datetime tests
# ---------------------------------------------------------------------------

def test_get_datetime_utc():
    result = get_datetime("UTC")
    assert "2026" in result
    assert "UTC" in result


def test_get_datetime_invalid_tz():
    """An invalid timezone should fall back to UTC without raising."""
    result = get_datetime("Not/AReal_Timezone")
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# API format tests
# ---------------------------------------------------------------------------

def test_anthropic_tools_format():
    tools = registry.get_anthropic_tools()
    assert isinstance(tools, list)
    assert len(tools) == 4
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool


def test_ollama_tools_format():
    tools = registry.get_ollama_tools()
    assert isinstance(tools, list)
    assert len(tools) == 4
    for tool in tools:
        assert tool.get("type") == "function"
        assert "function" in tool
        fn = tool["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
