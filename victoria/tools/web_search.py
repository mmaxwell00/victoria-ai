import logging
from victoria.tools.registry import registry

logger = logging.getLogger(__name__)


@registry.tool(
    name="web_search",
    description="Search the web for current information, news, facts, or any topic.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "max_results": {"type": "integer", "description": "Number of results to return (1-5)", "default": 3},
        },
        "required": ["query"],
    },
)
async def web_search(query: str, max_results: int = 3) -> str:
    from duckduckgo_search import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append(f"**{r['title']}**\n{r['body']}\nSource: {r['href']}")
    return "\n\n".join(results) if results else "No results found."
