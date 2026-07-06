from victoria.core.memory import MemoryStore
from victoria.core.llm_router import LLMRouter
from victoria.config import (
    settings,
    ESCALATION_SENTINEL,
    ESCALATION_INSTRUCTION,
)
from typing import AsyncIterator, Optional, TYPE_CHECKING
import asyncio
import logging
import uuid

logger = logging.getLogger(__name__)

# How the user answers Victoria's "shall I escalate?" prompt.
_AFFIRMATIVE = {
    "y", "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "pls",
    "go", "go on", "go ahead", "do it", "escalate", "claude", "use claude",
    "yes please", "please do", "fire away", "aye",
}
_NEGATIVE = {
    "n", "no", "nope", "nah", "cancel", "stop", "don't", "dont", "leave it",
    "no thanks", "no thank you", "forget it", "never mind", "nevermind",
}

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
        # session_id -> the original question awaiting an escalation yes/no.
        self._pending_escalation: dict[str, str] = {}

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    # ------------------------------------------------------------------ #
    # Escalation helpers                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _local_backend() -> str:
        """The local backend to try first (never a cloud one)."""
        return settings.default_llm if settings.default_llm in ("docker", "ollama") else "docker"

    @staticmethod
    def _needs_escalation(response: str) -> bool:
        """True when the local model signalled it couldn't answer."""
        stripped = (response or "").strip()
        return not stripped or ESCALATION_SENTINEL in stripped

    @staticmethod
    def _classify_reply(message: str) -> str:
        """Interpret a reply to the escalation prompt: 'yes' / 'no' / 'other'."""
        norm = message.strip().lower().rstrip("!.")
        if norm in _AFFIRMATIVE or norm.split()[:1] == ["claude"]:
            return "yes"
        if norm in _NEGATIVE:
            return "no"
        return "other"

    @staticmethod
    def _escalation_prompt() -> str:
        return (
            "I'm afraid that one's rather beyond my local wits just now. "
            "Shall I put it to Claude for a proper answer? (yes / no)"
        )

    async def _local_answer(self, history, system_prompt, force_backend):
        """Run the local model (with tools if available). Returns (text, backend)."""
        if self.tool_registry and len(self.tool_registry) > 0:
            return await self.router.chat_with_tools(
                history, self.tool_registry, system_prompt, force_backend
            )
        return await self.router.chat(
            history, force_backend=force_backend, system_prompt=system_prompt
        )

    def _finalize(self, session_id, user_id, user_message, response, backend) -> dict:
        """Persist a completed answer to memory + semantic store + profile."""
        self.memory.add_message(session_id, "assistant", response, llm_used=backend)
        if self.semantic_memory:
            self.semantic_memory.add(session_id, "user", user_message)
            self.semantic_memory.add(session_id, "assistant", response)
        if self.profile_extractor and self.profile_store:
            self._spawn_profile_update(user_id, user_message, response)
        return {"session_id": session_id, "response": response, "backend": backend}

    def _offer_escalation(self, session_id, user_message, local_backend) -> dict:
        """Record the pending question and ask the user for permission to escalate."""
        self._pending_escalation[session_id] = user_message
        prompt = self._escalation_prompt()
        self.memory.add_message(session_id, "assistant", prompt, llm_used="victoria")
        logger.info("Offering escalation for session %s (local=%s)", session_id, local_backend)
        return {"session_id": session_id, "response": prompt, "backend": "victoria"}

    async def _answer_with_claude(self, session_id, user_id, question, system_prompt) -> dict:
        """Answer via the Claude Code CLI and persist the result."""
        try:
            answer = await self.router.claude_cli(question, system_prompt=system_prompt)
        except Exception as exc:
            logger.exception("Claude CLI escalation failed")
            reply = f"I did try Claude, but it wouldn't play ball: {exc}"
            self.memory.add_message(session_id, "assistant", reply, llm_used="victoria")
            return {"session_id": session_id, "response": reply, "backend": "victoria"}
        return self._finalize(session_id, user_id, question, answer, "claude")

    def _victoria_system_prompt(self) -> str:
        from victoria.config import VICTORIA_SYSTEM_PROMPT
        return VICTORIA_SYSTEM_PROMPT

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

        # --- Reply to a pending "shall I escalate?" prompt ----------------
        if settings.escalation_enabled and session_id in self._pending_escalation:
            decision = self._classify_reply(user_message)
            if decision == "yes":
                question = self._pending_escalation.pop(session_id)
                self.memory.add_message(session_id, "user", user_message)
                sys_prompt = self._build_system_prompt(question, session_id, user_id)
                return await self._answer_with_claude(session_id, user_id, question, sys_prompt)
            if decision == "no":
                self._pending_escalation.pop(session_id, None)
                self.memory.add_message(session_id, "user", user_message)
                reply = "Righto — I'll leave that one be. What else can I do for you?"
                self.memory.add_message(session_id, "assistant", reply, llm_used="victoria")
                return {"session_id": session_id, "response": reply, "backend": "victoria"}
            # decision == "other": drop the pending offer, treat as a new question
            self._pending_escalation.pop(session_id, None)

        # Detect and store explicit memory requests (regex, instant)
        if self.profile_extractor and self.profile_store:
            memory = self.profile_extractor.detect_explicit_memory(user_message)
            if memory:
                self.profile_store.add_memory(user_id, memory)

        system_prompt = self._build_system_prompt(user_message, session_id, user_id)

        # Persist the user message up front (matching stream_chat) so it isn't
        # lost when the LLM call fails.
        self.memory.add_message(session_id, "user", user_message)

        # --- User explicitly chose Claude in the UI -----------------------
        if settings.escalation_enabled and force_backend == "claude":
            return await self._answer_with_claude(session_id, user_id, user_message, system_prompt)

        # --- Local-first, offer escalation if the local model can't cope --
        if settings.escalation_enabled and force_backend in (None, "", "auto", "docker", "ollama"):
            local_backend = force_backend if force_backend in ("docker", "ollama") else self._local_backend()
            local_system = system_prompt + ESCALATION_INSTRUCTION
            try:
                response, backend = await self._local_answer(history, local_system, local_backend)
            except Exception:
                logger.exception("Local backend failed; offering escalation")
                response, backend = "", local_backend
            if self._needs_escalation(response):
                return self._offer_escalation(session_id, user_message, local_backend)
            return self._finalize(session_id, user_id, user_message, response, backend)

        # --- Escalation disabled, or some other forced backend ------------
        response, backend = await self._local_answer(history, system_prompt, force_backend)
        return self._finalize(session_id, user_id, user_message, response, backend)

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

        # --- Reply to a pending "shall I escalate?" prompt ----------------
        if settings.escalation_enabled and session_id in self._pending_escalation:
            decision = self._classify_reply(user_message)
            if decision == "yes":
                question = self._pending_escalation.pop(session_id)
                sys_prompt = self._build_system_prompt(question, session_id, user_id)
                async for ev in self._stream_claude(session_id, user_id, question, sys_prompt):
                    yield ev
                return
            if decision == "no":
                self._pending_escalation.pop(session_id, None)
                reply = "Righto — I'll leave that one be. What else can I do for you?"
                self.memory.add_message(session_id, "assistant", reply, llm_used="victoria")
                yield {"session_id": session_id, "chunk": reply, "backend": "victoria", "done": False}
                yield {"session_id": session_id, "chunk": "", "backend": "victoria", "done": True}
                return
            # decision == "other": drop the pending offer, treat as a new question
            self._pending_escalation.pop(session_id, None)

        system_prompt = self._build_system_prompt(user_message, session_id, user_id)

        # --- User explicitly chose Claude in the UI -----------------------
        if settings.escalation_enabled and force_backend == "claude":
            async for ev in self._stream_claude(session_id, user_id, user_message, system_prompt):
                yield ev
            return

        # --- Local-first, offer escalation if the local model can't cope --
        if settings.escalation_enabled and force_backend in (None, "", "auto", "docker", "ollama"):
            local_backend = force_backend if force_backend in ("docker", "ollama") else self._local_backend()
            local_system = system_prompt + ESCALATION_INSTRUCTION
            sentinel_norm = ESCALATION_SENTINEL.upper()
            buffer = ""
            decided = False          # True once we know the output isn't the sentinel
            backend_used = local_backend
            failed = False

            try:
                async for chunk, backend in self.router.stream_chat(
                    history, force_backend=local_backend, system_prompt=local_system
                ):
                    backend_used = backend
                    buffer += chunk
                    if decided:
                        yield {"session_id": session_id, "chunk": chunk, "backend": backend, "done": False}
                        continue
                    stripped = buffer.strip()
                    if not stripped:
                        continue  # only whitespace so far — can't tell yet
                    if sentinel_norm.startswith(stripped.upper()):
                        continue  # could still grow into the sentinel — keep buffering
                    # Diverged from the sentinel: flush what we've held and stream on.
                    decided = True
                    yield {"session_id": session_id, "chunk": buffer, "backend": backend, "done": False}
            except Exception:
                logger.exception("Local streaming failed; offering escalation")
                failed = True

            complete = buffer.strip()

            # Nothing streamed yet and the local model failed / bailed → offer Claude.
            if not decided and (failed or self._needs_escalation(complete)):
                self._pending_escalation[session_id] = user_message
                prompt = self._escalation_prompt()
                self.memory.add_message(session_id, "assistant", prompt, llm_used="victoria")
                logger.info("Offering escalation for session %s (local=%s)", session_id, local_backend)
                yield {"session_id": session_id, "chunk": prompt, "backend": "victoria", "done": False}
                yield {"session_id": session_id, "chunk": "", "backend": "victoria", "done": True}
                return

            # Held a short, non-sentinel answer that never got flushed — flush now.
            if not decided and complete:
                yield {"session_id": session_id, "chunk": buffer, "backend": backend_used, "done": False}

            self.memory.add_message(session_id, "assistant", complete, llm_used=backend_used)
            if self.semantic_memory:
                self.semantic_memory.add(session_id, "user", user_message)
                self.semantic_memory.add(session_id, "assistant", complete)
            if self.profile_extractor and self.profile_store:
                self._spawn_profile_update(user_id, user_message, complete)
            yield {"session_id": session_id, "chunk": "", "backend": backend_used, "done": True}
            return

        # --- Escalation disabled, or some other forced backend ------------
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

    async def _stream_claude(self, session_id, user_id, question, system_prompt) -> AsyncIterator[dict]:
        """Escalate to the Claude Code CLI and emit the answer as stream events.

        The CLI is not itself streaming, so the answer arrives as a single chunk.
        """
        try:
            answer = await self.router.claude_cli(question, system_prompt=system_prompt)
            backend = "claude"
        except Exception as exc:
            logger.exception("Claude CLI escalation failed")
            answer = f"I did try Claude, but it wouldn't play ball: {exc}"
            backend = "victoria"

        self.memory.add_message(session_id, "assistant", answer, llm_used=backend)
        if backend == "claude":
            if self.semantic_memory:
                self.semantic_memory.add(session_id, "user", question)
                self.semantic_memory.add(session_id, "assistant", answer)
            if self.profile_extractor and self.profile_store:
                self._spawn_profile_update(user_id, question, answer)

        yield {"session_id": session_id, "chunk": answer, "backend": backend, "done": False}
        yield {"session_id": session_id, "chunk": "", "backend": backend, "done": True}

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
