import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema
    fn: Callable


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def tool(self, name: str, description: str, parameters: dict) -> Callable:
        """Decorator: register a function as a Victoria tool."""
        def decorator(fn: Callable) -> Callable:
            self._tools[name] = ToolDef(name=name, description=description,
                                         parameters=parameters, fn=fn)
            logger.debug("Registered tool: %s", name)
            return fn
        return decorator

    def get_anthropic_tools(self) -> list[dict]:
        """Return tools in Anthropic API format."""
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in self._tools.values()
        ]

    def get_ollama_tools(self) -> list[dict]:
        """Return tools in Ollama/OpenAI function-calling format."""
        return [
            {"type": "function", "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }}
            for t in self._tools.values()
        ]

    async def execute(self, name: str, **kwargs) -> str:
        """Execute a tool by name. Returns string result or error message."""
        if name not in self._tools:
            return f"Unknown tool: {name}"
        try:
            result = self._tools[name].fn(**kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        except Exception as exc:
            logger.exception("Tool %s raised an error", name)
            return f"Tool error ({name}): {exc}"

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# Module-level singleton
registry = ToolRegistry()
