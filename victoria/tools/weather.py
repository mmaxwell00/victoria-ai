import httpx
from victoria.tools.registry import registry


@registry.tool(
    name="get_weather",
    description="Get the current weather conditions for any city or location.",
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name or location, e.g. 'London' or 'New York'"},
        },
        "required": ["location"],
    },
)
async def get_weather(location: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"https://wttr.in/{location}?format=3")
        resp.raise_for_status()
        return resp.text.strip()
