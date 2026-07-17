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
            self.add(name, description, parameters, fn)
            return fn
        return decorator

    def add(self, name: str, description: str, parameters: dict, fn: Callable) -> None:
        """Programmatically register a tool (used for dynamic MCP tools)."""
        self._tools[name] = ToolDef(name=name, description=description,
                                    parameters=parameters, fn=fn)
        logger.debug("Registered tool: %s", name)

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

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

    @staticmethod
    def _coerce_args(schema: dict, kwargs: dict) -> dict:
        """Coerce string-typed argument values to the types the schema declares.

        Small local models often emit every tool argument as a string
        ("max_results": "3"); left as-is these crash tools (and libraries
        underneath them) that expect real numbers or booleans. Values that
        don't parse are passed through unchanged so the tool's own error
        handling still applies.
        """
        props = schema.get("properties") or {}
        out = {}
        for key, value in kwargs.items():
            declared = (props.get(key) or {}).get("type")
            if isinstance(value, str):
                try:
                    if declared == "integer":
                        value = int(value)
                    elif declared == "number":
                        value = float(value)
                    elif declared == "boolean" and value.lower() in ("true", "false"):
                        value = value.lower() == "true"
                except ValueError:
                    pass
            out[key] = value
        return out

    async def execute(self, name: str, **kwargs) -> str:
        """Execute a tool by name. Returns string result or error message."""
        logger.info("Tool call: %s(%s)", name, ", ".join(kwargs))
        if name not in self._tools:
            return f"Unknown tool: {name}"
        try:
            kwargs = self._coerce_args(self._tools[name].parameters, kwargs)
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
