from victoria.core.memory import MemoryStore
from victoria.core.llm_router import LLMRouter
from typing import AsyncIterator, Optional, TYPE_CHECKING
import asyncio
import uuid

if TYPE_CHECKING:
    from victoria.tools.registry import ToolRegistry
    from victoria.core.semantic_memory import SemanticMemory
    from victoria.core.user_profile import ProfileStore
    from victoria.core.profile_extractor import ProfileExtractor


class ConversationManager:
    def __init__(
        self,
        memory: MemoryStore,
        router: LLMRouter,
        tool_registry: Optional["ToolRegistry"] = None,
        semantic_memory: Optional["SemanticMemory"] = None,
        profile_store: Optional["ProfileStore"] = None,
        profile_extractor: Optional["ProfileExtractor"] = None,
    ):
        self.memory = memory
        self.router = router
        self.tool_registry = tool_registry
        self.semantic_memory = semantic_memory
        self.profile_store = profile_store
        self.profile_extractor = profile_extractor
        # Keep references to fire-and-forget tasks — asyncio only holds weak
        # references, so unreferenced tasks can be garbage-collected mid-run.
        self._background_tasks: set = set()

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    def _spawn_profile_update(self, user_id: str, user_message: str, response: str) -> None:
        task = asyncio.create_task(self._update_profile_async(user_id, user_message, response))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _build_system_prompt(self, user_message: str, session_id: str, user_id: str = "default") -> str:
        from victoria.config import VICTORIA_SYSTEM_PROMPT
        base = VICTORIA_SYSTEM_PROMPT

        # 1. Inject user profile
        if self.profile_store:
            profile = self.profile_store.get(user_id)
            profile_context = profile.to_system_context()
            if profile_context:
                base = base + "\n\n" + profile_context

        # 2. Inject semantic memory (existing logic, unchanged)
        if self.semantic_memory and self.semantic_memory.available:
            memories = self.semantic_memory.search(user_message, n=3, exclude_session=session_id)
            if memories:
                context = "\n".join(f"- {m['content'][:200]}" for m in memories)
                base = base + f"\n\nRelevant context from past conversations:\n{context}"

        return base

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

        # Detect and store explicit memory requests (regex, instant)
        if self.profile_extractor and self.profile_store:
            memory = self.profile_extractor.detect_explicit_memory(user_message)
            if memory:
                self.profile_store.add_memory(user_id, memory)

        system_prompt = self._build_system_prompt(user_message, session_id, user_id)

        # Persist the user message up front (matching stream_chat) so it isn't
        # lost when the LLM call fails.
        self.memory.add_message(session_id, "user", user_message)

        if self.tool_registry and len(self.tool_registry) > 0:
            response, backend = await self.router.chat_with_tools(
                history, self.tool_registry, system_prompt, force_backend
            )
        else:
            response, backend = await self.router.chat(
                history, force_backend=force_backend, system_prompt=system_prompt
            )

        self.memory.add_message(session_id, "assistant", response, llm_used=backend)

        if self.semantic_memory:
            self.semantic_memory.add(session_id, "user", user_message)
            self.semantic_memory.add(session_id, "assistant", response)

        if self.profile_extractor and self.profile_store:
            self._spawn_profile_update(user_id, user_message, response)

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

        system_prompt = self._build_system_prompt(user_message, session_id, user_id)

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

        if self.profile_extractor and self.profile_store:
            self._spawn_profile_update(user_id, user_message, complete)

        yield {"session_id": session_id, "chunk": "", "backend": backend_used, "done": True}

    async def _update_profile_async(self, user_id: str, user_message: str, response: str) -> None:
        try:
            profile = self.profile_store.get(user_id)
            updated = await self.profile_extractor.extract_from_turn(
                profile, user_message, response, user_id=user_id
            )
            if not updated.is_empty() and updated != profile:
                self.profile_store.save(updated)
        except Exception:
            import logging
            logging.getLogger(__name__).debug("Background profile update failed", exc_info=True)
