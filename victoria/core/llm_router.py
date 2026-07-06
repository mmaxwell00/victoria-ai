import httpx
from typing import AsyncIterator, Optional, TYPE_CHECKING
from anthropic import AsyncAnthropic

from victoria.config import settings, VICTORIA_SYSTEM_PROMPT

if TYPE_CHECKING:
    from victoria.tools.registry import ToolRegistry


class LLMRouter:
    """Routes queries to Ollama (local) or Claude (cloud) based on config or complexity."""

    def __init__(self):
        self._anthropic: Optional[AsyncAnthropic] = None
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def anthropic(self) -> AsyncAnthropic:
        if self._anthropic is None:
            self._anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._anthropic

    @property
    def http(self) -> httpx.AsyncClient:
        """Shared client for the local backends — reuses connections instead
        of building a new pool per request."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=60.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    def _pick_backend(self, message: str, force: Optional[str] = None) -> str:
        if force:
            return force
        if settings.default_llm == "claude":
            return "claude"
        # Route to Claude if query is long/complex (word count as a rough
        # complexity proxy) — but only if an API key is actually configured.
        if (
            settings.anthropic_api_key
            and len(message.split()) > settings.complex_query_threshold
        ):
            return "claude"
        return settings.default_llm  # "ollama" or "docker"

    async def chat(
        self,
        messages: list[dict],
        force_backend: Optional[str] = None,
        stream: bool = False,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        Returns (response_text, backend_used).
        Uses the last user message to decide routing.
        """
        last_user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        backend = self._pick_backend(last_user_msg, force_backend)

        if backend == "claude":
            text = await self._claude(messages, system_prompt=system_prompt)
        elif backend == "docker":
            text = await self._docker(messages, system_prompt=system_prompt)
        else:
            text = await self._ollama(messages, system_prompt=system_prompt)

        return text, backend

    async def stream_chat(
        self,
        messages: list[dict],
        force_backend: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """Yields (chunk, backend_used) tuples."""
        last_user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        backend = self._pick_backend(last_user_msg, force_backend)

        if backend == "claude":
            async for chunk in self._claude_stream(messages, system_prompt=system_prompt):
                yield chunk, backend
        elif backend == "docker":
            async for chunk in self._docker_stream(messages, system_prompt=system_prompt):
                yield chunk, backend
        else:
            async for chunk in self._ollama_stream(messages, system_prompt=system_prompt):
                yield chunk, backend

    async def chat_with_tools(
        self,
        messages: list[dict],
        tool_registry: "ToolRegistry",
        system_prompt: str,
        force_backend: Optional[str] = None,
    ) -> tuple[str, str]:
        """Run the tool-calling loop. Returns (final_response_text, backend_used)."""
        last_user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        backend = self._pick_backend(last_user_msg, force_backend)

        if backend == "claude":
            text = await self._claude_with_tools(messages, tool_registry, system_prompt)
        elif backend == "docker":
            text = await self._docker_with_tools(messages, tool_registry, system_prompt)
        else:
            text = await self._ollama_with_tools(messages, tool_registry, system_prompt)

        return text, backend

    async def _ollama(self, messages: list[dict], system_prompt: Optional[str] = None) -> str:
        payload = {
            "model": settings.ollama_model,
            "messages": [{"role": "system", "content": system_prompt or VICTORIA_SYSTEM_PROMPT}] + messages,
            "stream": False,
        }
        resp = await self.http.post(
            f"{settings.ollama_base_url}/api/chat", json=payload
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    async def _ollama_stream(self, messages: list[dict], system_prompt: Optional[str] = None) -> AsyncIterator[str]:
        import json as _json

        payload = {
            "model": settings.ollama_model,
            "messages": [{"role": "system", "content": system_prompt or VICTORIA_SYSTEM_PROMPT}] + messages,
            "stream": True,
        }
        async with self.http.stream(
            "POST", f"{settings.ollama_base_url}/api/chat", json=payload,
            timeout=120.0,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.strip():
                    data = _json.loads(line)
                    if chunk := data.get("message", {}).get("content"):
                        yield chunk

    async def _claude(self, messages: list[dict], system_prompt: Optional[str] = None) -> str:
        response = await self.anthropic.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            system=system_prompt or VICTORIA_SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text

    async def _claude_stream(self, messages: list[dict], system_prompt: Optional[str] = None) -> AsyncIterator[str]:
        async with self.anthropic.messages.stream(
            model=settings.claude_model,
            max_tokens=1024,
            system=system_prompt or VICTORIA_SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def _claude_with_tools(
        self,
        messages: list[dict],
        tool_registry: "ToolRegistry",
        system_prompt: str,
    ) -> str:
        working_messages = list(messages)
        response = None
        for _ in range(5):
            response = await self.anthropic.messages.create(
                model=settings.claude_model,
                max_tokens=1024,
                system=system_prompt,
                messages=working_messages,
                tools=tool_registry.get_anthropic_tools(),
            )
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""
            if response.stop_reason == "tool_use":
                # Add assistant turn (content is list of blocks — pass as-is to Anthropic)
                working_messages.append({"role": "assistant", "content": response.content})
                # Execute tools, collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = await tool_registry.execute(block.name, **block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                working_messages.append({"role": "user", "content": tool_results})
            else:
                break
        # Fallback: extract any text from last response
        if response:
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
        return "I ran into a spot of bother processing that. Do try again."

    async def _ollama_with_tools(
        self,
        messages: list[dict],
        tool_registry: "ToolRegistry",
        system_prompt: str,
    ) -> str:
        import json as _json

        working_messages = [{"role": "system", "content": system_prompt}] + list(messages)
        last_message: dict = {}
        for _ in range(5):
            payload = {
                "model": settings.ollama_model,
                "messages": working_messages,
                "tools": tool_registry.get_ollama_tools(),
                "stream": False,
            }
            resp = await self.http.post(
                f"{settings.ollama_base_url}/api/chat", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            last_message = data["message"]
            tool_calls = last_message.get("tool_calls") or []
            if not tool_calls:
                return last_message.get("content", "")
            working_messages.append(last_message)
            for tc in tool_calls:
                fn = tc["function"]
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = _json.loads(args)
                result = await tool_registry.execute(fn["name"], **args)
                working_messages.append({"role": "tool", "content": result})
        return last_message.get("content", "")

    # ------------------------------------------------------------------ #
    # Docker Model Runner (OpenAI-compatible API)                         #
    # ------------------------------------------------------------------ #

    async def _docker(self, messages: list[dict], system_prompt: Optional[str] = None) -> str:
        payload = {
            "model": settings.model_runner_model,
            "messages": [{"role": "system", "content": system_prompt or VICTORIA_SYSTEM_PROMPT}] + messages,
            "stream": False,
        }
        resp = await self.http.post(
            f"{settings.model_runner_url}/chat/completions", json=payload
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def _docker_stream(self, messages: list[dict], system_prompt: Optional[str] = None) -> AsyncIterator[str]:
        import json as _json

        payload = {
            "model": settings.model_runner_model,
            "messages": [{"role": "system", "content": system_prompt or VICTORIA_SYSTEM_PROMPT}] + messages,
            "stream": True,
        }
        async with self.http.stream(
            "POST", f"{settings.model_runner_url}/chat/completions", json=payload,
            timeout=120.0,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    data = _json.loads(line[6:])
                    if chunk := data.get("choices", [{}])[0].get("delta", {}).get("content"):
                        yield chunk

    async def _docker_with_tools(
        self,
        messages: list[dict],
        tool_registry: "ToolRegistry",
        system_prompt: str,
    ) -> str:
        import json as _json

        working_messages = [{"role": "system", "content": system_prompt}] + list(messages)
        last_message: dict = {}
        for _ in range(5):
            payload = {
                "model": settings.model_runner_model,
                "messages": working_messages,
                "tools": tool_registry.get_ollama_tools(),  # OpenAI format
                "stream": False,
            }
            resp = await self.http.post(
                f"{settings.model_runner_url}/chat/completions", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            last_message = choice["message"]
            tool_calls = last_message.get("tool_calls") or []
            if not tool_calls or choice.get("finish_reason") == "stop":
                return last_message.get("content") or ""
            working_messages.append(last_message)
            for tc in tool_calls:
                fn = tc["function"]
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = _json.loads(args)
                result = await tool_registry.execute(fn["name"], **args)
                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
        return last_message.get("content") or ""
