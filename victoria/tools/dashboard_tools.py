"""Tools that let the operator manage the HUD dashboard by talking to Victoria,
e.g. "track the weather in Dallas", "add Apple to my stocks", "follow Fox News",
"stop tracking Tesla". The LLM maps a company name to its ticker symbol."""
from victoria.tools.registry import registry
from victoria.dashboard.store import dashboard_store

_KIND_DESC = (
    "What to track: 'city' (weather), 'stock' (a ticker symbol), or 'news' "
    "(a news source)."
)
_VALUE_DESC = (
    "The thing to track. For a city, the city name (e.g. 'Dallas'). For a stock, "
    "the TICKER SYMBOL, not the company name (e.g. 'AAPL' for Apple, 'TSLA' for "
    "Tesla) — convert the company name to its symbol yourself. For news, the "
    "outlet name ('CNN' or 'Fox News')."
)
_PARAMS = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["city", "stock", "news"], "description": _KIND_DESC},
        "value": {"type": "string", "description": _VALUE_DESC},
    },
    "required": ["kind", "value"],
}


@registry.tool(
    name="track_dashboard",
    description=(
        "Add an item to the operator's HUD dashboard so Victoria keeps it on "
        "screen: a city's weather, a stock's share price, or a news source's "
        "headlines. Call this whenever the user wants something ADDED to or "
        "SHOWN on their dashboard — 'track Dallas', 'add Apple stock', 'follow "
        "Fox News', 'include Miami in the weather', 'put Tesla on my dashboard', "
        "'update the dashboard with Chicago', 'show London'. This is the ONLY "
        "way to change the dashboard — never claim you added something without "
        "calling it."
    ),
    parameters=_PARAMS,
)
async def track_dashboard(kind: str, value: str) -> str:
    _, msg = dashboard_store.add(kind, value)
    return msg


@registry.tool(
    name="untrack_dashboard",
    description=(
        "Remove an item from the operator's HUD dashboard (a city, a stock, or a "
        "news source). Call this whenever the user wants something taken OFF the "
        "dashboard — 'stop tracking London', 'drop Tesla', 'remove CNN', "
        "'unfollow Fox News', 'take Miami off the weather'. This is the ONLY way "
        "to remove something — never claim you removed it without calling it."
    ),
    parameters=_PARAMS,
)
async def untrack_dashboard(kind: str, value: str) -> str:
    _, msg = dashboard_store.remove(kind, value)
    return msg
