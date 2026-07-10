import asyncio
import logging
import os
import tempfile

import httpx
from typing import AsyncIterator, Optional, TYPE_CHECKING
from anthropic import AsyncAnthropic

from victoria.config import settings, VICTORIA_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from victoria.tools.registry import ToolRegistry


# Environment variables that hijack the Claude Code CLI's authentication if they
# leak in from the launch environment — most commonly when Victoria is started
# from inside another Claude Code / Agent SDK session, which injects a
# session-scoped credential context and a gateway base URL. Left in place, they
# override the machine's own subscription login and the CLI 401s. We scrub them
# for the `claude -p` subprocess so escalation always uses the real login.
# CLAUDE_CODE_OAUTH_TOKEN is deliberately kept — that's the supported headless
# token-auth path (e.g. the Docker deployment injects it).
_CLAUDE_CLI_BLOCKED_ENV = frozenset({
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",          # subscription auth only; a stray key would 401
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_CODE_OAUTH_SCOPES",
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
})


def _claude_cli_env(base: Optional[dict] = None) -> dict:
    """Return a copy of the environment with the credential-hijacking variables
    removed, so the Claude Code CLI authenticates with the machine's own
    subscription login instead of an inherited session/gateway context."""
    source = os.environ if base is None else base
    return {k: v for k, v in source.items() if k not in _CLAUDE_CLI_BLOCKED_ENV}


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

    async def available_models(self) -> list[dict]:
        """List models the Docker Model Runner has pulled.

        Returns [{id, size_gib, params, context}], best-effort — [] on error.
        """
        import re as _re
        try:
            resp = await self.http.get(f"{settings.model_runner_url}/models")
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except Exception:
            logger.exception("Could not list Model Runner models")
            return []
        out = []
        for m in data:
            dmr = m.get("dmr", {}) or {}
            size = None
            match = _re.search(r"([\d.]+)\s*GiB", str(dmr.get("size", "")))
            if match:
                size = float(match.group(1))
            out.append({
                "id": m.get("id", ""),
                "size_gib": size,
                "params": dmr.get("parameters"),
                "context": dmr.get("context_window"),
            })
        return out

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
    # Claude Code CLI (uses the local Claude subscription — no API key)   #
    # ------------------------------------------------------------------ #

    async def claude_cli(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Answer via the Claude Code CLI in headless print mode.

        Uses the machine's Claude subscription auth (never the API key), so it
        works without ANTHROPIC_API_KEY. Runs from a neutral temp directory so
        it doesn't pick up the current project's CLAUDE.md context, and with a
        scrubbed environment (see _claude_cli_env) so a session/gateway context
        inherited from the launch shell can't hijack authentication.
        """
        args = [
            settings.claude_cli_command,
            "-p", prompt,
            "--model", settings.claude_cli_model,
        ]
        if system_prompt:
            args += ["--append-system-prompt", system_prompt]
        # Pre-approve read-only tools so Claude can answer real-time questions
        # without stalling on a permission prompt it can't answer in headless mode.
        allowed = (settings.claude_cli_allowed_tools or "").replace(",", " ").split()
        if allowed:
            args += ["--allowedTools", *allowed]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tempfile.gettempdir(),
                env=_claude_cli_env(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Claude Code CLI ('{settings.claude_cli_command}') was not found on "
                f"PATH. Install it, or set CLAUDE_CLI_COMMAND in .env."
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.claude_cli_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"Claude Code CLI timed out after {settings.claude_cli_timeout}s."
            )

        if proc.returncode != 0:
            detail = (stderr.decode(errors="replace").strip() or "no error output")[:300]
            raise RuntimeError(f"Claude Code CLI failed (exit {proc.returncode}): {detail}")

        return stdout.decode(errors="replace").strip()

    # ------------------------------------------------------------------ #
    # Docker Model Runner (OpenAI-compatible API)                         #
    # ------------------------------------------------------------------ #

    def _raise_for_docker_status(self, resp: httpx.Response) -> None:
        """Raise a clear, actionable error for Model Runner failures.

        A 404 almost always means MODEL_RUNNER_MODEL doesn't match a model the
        runner actually has loaded — the id must match `docker model ls` exactly,
        including any tag (e.g. ai/llama3.2:3B-Q4_K_M). Without this, a typo'd
        model name surfaces to the caller as an opaque 500.
        """
        if resp.status_code == 404:
            raise RuntimeError(
                f"Docker Model Runner has no model named "
                f"'{settings.model_runner_model}'. Run `docker model ls` and set "
                f"MODEL_RUNNER_MODEL in .env to the exact id shown there "
                f"(including any tag)."
            )
        resp.raise_for_status()

    async def _docker(self, messages: list[dict], system_prompt: Optional[str] = None) -> str:
        payload = {
            "model": settings.model_runner_model,
            "messages": [{"role": "system", "content": system_prompt or VICTORIA_SYSTEM_PROMPT}] + messages,
            "stream": False,
        }
        resp = await self.http.post(
            f"{settings.model_runner_url}/chat/completions", json=payload
        )
        self._raise_for_docker_status(resp)
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
            self._raise_for_docker_status(resp)
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
            self._raise_for_docker_status(resp)
            data = resp.json()
            choice = data["choices"][0]
            last_message = choice["message"]
            tool_calls = last_message.get("tool_calls") or []
            # Execute tools whenever they're present — the Model Runner may tag
            # the turn finish_reason "stop" even alongside tool_calls, so don't
            # gate on finish_reason or the calls get silently dropped.
            if not tool_calls:
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
