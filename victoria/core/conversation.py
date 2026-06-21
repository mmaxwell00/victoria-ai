from victoria.core.memory import MemoryStore
from victoria.core.llm_router import LLMRouter
from typing import AsyncIterator, Optional, TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from victoria.tools.registry import ToolRegistry
    from victoria.core.semantic_memory import SemanticMemory


class ConversationManager:
    def __init__(
        self,
        memory: MemoryStore,
        router: LLMRouter,
        tool_registry: Optional["ToolRegistry"] = None,
        semantic_memory: Optional["SemanticMemory"] = None,
    ):
        self.memory = memory
        self.router = router
        self.tool_registry = tool_registry
        self.semantic_memory = semantic_memory

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    def _build_system_prompt(self, user_message: str, session_id: str) -> str:
        from victoria.config import VICTORIA_SYSTEM_PROMPT
        if not self.semantic_memory or not self.semantic_memory.available:
            return VICTORIA_SYSTEM_PROMPT
        memories = self.semantic_memory.search(user_message, n=3, exclude_session=session_id)
        if not memories:
            return VICTORIA_SYSTEM_PROMPT
        context = "\n".join(f"- {m['content'][:200]}" for m in memories)
        return VICTORIA_SYSTEM_PROMPT + f"\n\nRelevant context from past conversations:\n{context}"

    async def chat(
        self,
        user_message: str,
        session_id: Optional[str] = None,
        user_id: str = "default",
        channel: str = "api",
        force_backend: Optional[str] = None,
    ) -> dict:
        session_id = session_id or self.new_session_id()
        self.memory.get_or_create_session(session_id, user_id, channel)

        history = self.memory.get_history(session_id)
        history.append({"role": "user", "content": user_message})

        system_prompt = self._build_system_prompt(user_message, session_id)

        if self.tool_registry and len(self.tool_registry) > 0:
            response, backend = await self.router.chat_with_tools(
                history, self.tool_registry, system_prompt, force_backend
            )
        else:
            response, backend = await self.router.chat(
                history, force_backend=force_backend, system_prompt=system_prompt
            )

        self.memory.add_message(session_id, "user", user_message)
        self.memory.add_message(session_id, "assistant", response, llm_used=backend)

        if self.semantic_memory:
            self.semantic_memory.add(session_id, "user", user_message)
            self.semantic_memory.add(session_id, "assistant", response)

        return {
            "session_id": session_id,
            "response": response,
            "backend": backend,
        }

    async def stream_chat(
        self,
        user_message: str,
        session_id: Optional[str] = None,
        user_id: str = "default",
        channel: str = "api",
        force_backend: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        session_id = session_id or self.new_session_id()
        self.memory.get_or_create_session(session_id, user_id, channel)

        history = self.memory.get_history(session_id)
        history.append({"role": "user", "content": user_message})

        self.memory.add_message(session_id, "user", user_message)

        system_prompt = self._build_system_prompt(user_message, session_id)

        full_response = []
        backend_used = "ollama"

        async for chunk, backend in self.router.stream_chat(
            history, force_backend=force_backend, system_prompt=system_prompt
        ):
            full_response.append(chunk)
            backend_used = backend
            yield {"session_id": session_id, "chunk": chunk, "backend": backend, "done": False}

        complete = "".join(full_response)
        self.memory.add_message(session_id, "assistant", complete, llm_used=backend_used)

        if self.semantic_memory:
            self.semantic_memory.add(session_id, "user", user_message)
            self.semantic_memory.add(session_id, "assistant", complete)

        yield {"session_id": session_id, "chunk": "", "backend": backend_used, "done": True}
