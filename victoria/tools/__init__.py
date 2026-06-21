def load_all_tools() -> None:
    """Import all tool modules to trigger decorator-based registration."""
    from victoria.tools import web_search, weather, datetime_tool, calculator
