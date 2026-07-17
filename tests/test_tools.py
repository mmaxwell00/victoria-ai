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

CORE_TOOLS = {"web_search", "get_weather", "get_datetime", "calculate"}
SKILL_TOOLS = {"use_skill", "save_skill", "list_skills", "delete_skill"}


def test_registry_registers_tool():
    """After load_all_tools(), the registry contains the core + skill tools."""
    names = set(registry._tools)
    assert CORE_TOOLS <= names
    assert SKILL_TOOLS <= names


async def test_registry_execute_unknown_tool():
    """Executing an unknown tool name returns an error string without raising."""
    result = await registry.execute("does_not_exist")
    assert "Unknown tool" in result
    assert "does_not_exist" in result


async def test_registry_coerces_string_args_to_schema_types():
    """Small local models send every arg as a string; the registry coerces
    them to the schema-declared type before calling the tool."""
    received = {}

    def probe(count: int = 1, ratio: float = 1.0, flag: bool = False, label: str = "x"):
        received.update(count=count, ratio=ratio, flag=flag, label=label)
        return "ok"

    registry.add("probe_types", "test probe", {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "ratio": {"type": "number"},
            "flag": {"type": "boolean"},
            "label": {"type": "string"},
        },
    }, probe)
    try:
        await registry.execute(
            "probe_types", count="3", ratio="0.5", flag="true", label="7"
        )
    finally:
        registry.remove("probe_types")

    assert received == {"count": 3, "ratio": 0.5, "flag": True, "label": "7"}


async def test_registry_passes_unparseable_strings_through():
    """A string that can't be coerced reaches the tool unchanged (the tool's
    own error handling applies) instead of crashing the registry."""
    received = {}

    def probe(count: int = 1):
        received["count"] = count
        return "ok"

    registry.add("probe_bad", "test probe", {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
    }, probe)
    try:
        await registry.execute("probe_bad", count="lots")
    finally:
        registry.remove("probe_bad")

    assert received["count"] == "lots"


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
    assert len(tools) == len(registry._tools)
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool


def test_ollama_tools_format():
    tools = registry.get_ollama_tools()
    assert isinstance(tools, list)
    assert len(tools) == len(registry._tools)
    for tool in tools:
        assert tool.get("type") == "function"
        assert "function" in tool
        fn = tool["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn


# ------------------------------------------------------------------ #
# Calculator — exponentiation bounds (DoS guard)                       #
# ------------------------------------------------------------------ #

def test_calculate_normal_pow_still_works():
    from victoria.tools.calculator import calculate
    assert calculate("2 ** 10") == "2 ** 10 = 1024"


def test_calculate_huge_exponent_rejected():
    """Regression: '9**9**9' must return an error instantly, not hang."""
    import time
    from victoria.tools.calculator import calculate
    start = time.monotonic()
    result = calculate("9**9**9")
    elapsed = time.monotonic() - start
    assert "error" in result.lower()
    assert elapsed < 1.0, f"took {elapsed:.1f}s — DoS guard not effective"


def test_calculate_huge_base_rejected():
    from victoria.tools.calculator import calculate
    # inner 2**1000 ~ 1e301 exceeds the base bound for the outer **
    result = calculate("(2**1000) ** 500")
    assert "error" in result.lower()


# ------------------------------------------------------------------ #
# Weather — URL encoding                                               #
# ------------------------------------------------------------------ #

async def test_weather_url_encodes_location():
    """Regression: 'New York' and path metacharacters must be percent-encoded."""
    from unittest.mock import AsyncMock, MagicMock, patch
    import victoria.tools.weather as weather_mod

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            captured["url"] = url
            resp = MagicMock()
            resp.text = "New York: ☀️ +25°C"
            # No JSON here → get_weather falls back to the ?format=3 one-liner,
            # which is the URL these assertions check.
            resp.json = MagicMock(side_effect=ValueError("not json"))
            resp.raise_for_status = MagicMock()
            return resp

    with patch.object(weather_mod.httpx, "AsyncClient", FakeClient):
        await weather_mod.get_weather("New York")
        assert captured["url"] == "https://wttr.in/New%20York?format=3"

        await weather_mod.get_weather("x/../etc?foo=bar")
        assert "?foo" not in captured["url"].replace("?format=3", "")
        assert "/.." not in captured["url"]


@pytest.mark.asyncio
async def test_weather_includes_current_and_forecast():
    """get_weather returns current conditions plus a today/tomorrow forecast."""
    from unittest.mock import MagicMock, patch
    import victoria.tools.weather as weather_mod

    def day(date, hi, lo, desc):
        hourly = [{} for _ in range(6)]
        hourly[4] = {"weatherDesc": [{"value": desc}]}
        return {"date": date, "maxtempF": hi, "mintempF": lo, "hourly": hourly}

    j1 = {
        "current_condition": [
            {"temp_F": "86", "FeelsLikeF": "92", "weatherDesc": [{"value": "Partly cloudy"}]}
        ],
        "weather": [
            day("2026-07-12", "91", "72", "Sunny"),
            day("2026-07-13", "79", "71", "Light rain shower"),
        ],
    }

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            resp = MagicMock()
            resp.json = MagicMock(return_value=j1)
            resp.raise_for_status = MagicMock()
            return resp

    with patch.object(weather_mod.httpx, "AsyncClient", FakeClient):
        out = await weather_mod.get_weather("Atlanta")

    assert "now: 86°F" in out
    assert "Tomorrow (2026-07-13)" in out
    assert "79°F" in out and "Light rain shower" in out
