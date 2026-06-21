from victoria.tools.registry import registry


@registry.tool(
    name="get_datetime",
    description="Get the current date, time, and day of the week.",
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name, e.g. 'UTC', 'America/New_York', 'Europe/London'. Defaults to UTC.",
                "default": "UTC",
            },
        },
        "required": [],
    },
)
def get_datetime(timezone: str = "UTC") -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    return now.strftime("%A, %d %B %Y at %I:%M %p %Z")
