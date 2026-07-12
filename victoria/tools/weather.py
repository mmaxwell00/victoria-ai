from urllib.parse import quote

import httpx
from victoria.tools.registry import registry


@registry.tool(
    name="get_weather",
    description=(
        "Get the weather for any city or location: current conditions PLUS a "
        "short forecast for today and the next two days (highs, lows, and "
        "conditions). Use this for ANY weather question — now, today, tomorrow, "
        "or later this week."
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name or location, e.g. 'London' or 'Atlanta'"},
        },
        "required": ["location"],
    },
)
async def get_weather(location: str) -> str:
    loc = quote(location, safe="")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"https://wttr.in/{loc}?format=j1")
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            # Fall back to the one-line current-conditions format if the JSON
            # endpoint is unavailable or unparseable.
            resp = await client.get(f"https://wttr.in/{loc}?format=3")
            resp.raise_for_status()
            return resp.text.strip()

    cur = data["current_condition"][0]
    lines = [
        f"{location} — now: {cur['temp_F']}°F "
        f"(feels like {cur['FeelsLikeF']}°F), {cur['weatherDesc'][0]['value']}."
    ]
    labels = ["Today", "Tomorrow"]
    for i, day in enumerate(data.get("weather", [])[:3]):
        label = labels[i] if i < len(labels) else day["date"]
        hourly = day.get("hourly") or []
        desc = hourly[4]["weatherDesc"][0]["value"].strip() if len(hourly) > 4 else ""
        line = f"{label} ({day['date']}): high {day['maxtempF']}°F / low {day['mintempF']}°F"
        if desc:
            line += f", {desc}"
        lines.append(line + ".")
    return "\n".join(lines)
