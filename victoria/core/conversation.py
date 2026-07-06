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
import re
import uuid

logger = logging.getLogger(__name__)

# How the user answers Victoria's "shall I escalate?" prompt. Speech-to-text
# adds punctuation/casing ("Yes.", "Yeah!"), so we normalise before matching.
# Single words that clearly mean yes/no on their own.
_AFFIRMATIVE_WORDS = {
    "y", "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "pls",
    "escalate", "claude", "aye", "affirmative", "absolutely", "definitely",
    "yea", "yup", "ya",
}
_NEGATIVE_WORDS = {
    "n", "no", "nope", "nah", "cancel", "stop", "dont", "forget", "nevermind",
    "negative",
}
# Multi-word answers (already punctuation-stripped + single-spaced).
_AFFIRMATIVE_PHRASES = {
    "go on", "go ahead", "do it", "use claude", "yes please", "please do",
    "fire away", "go for it", "yes claude", "ask claude",
}
_NEGATIVE_PHRASES = {
    "leave it", "no thanks", "no thank you", "forget it", "never mind",
    "leave it be",
}

# The local model's "I can't answer" signal, matched loosely: small models
# often drop the brackets, add punctuation, OR bury the token at the end of a
# chatty non-answer (e.g. "…shall I escalate? [ESCALATE]").
def _escalation_alpha(text: str) -> str:
    return re.sub(r"[^A-Za-z]", "", text or "").upper()

# The bracketed token, tolerant of spacing/case: "[ESCALATE]", "[ escalate ]".
_SENTINEL_RE = re.compile(r"\[\s*escalate\s*\]", re.IGNORECASE)

# When the user declines escalation, the local model answers best-effort and
# must NOT emit the escalation token (we don't want to re-offer in a loop).
_BEST_EFFORT_INSTRUCTION = (
    "\n\nThe user has declined escalating to another model, so answer this "
    "question yourself as best you can from your own knowledge. Do NOT use any "
    "escalation token. If you're genuinely unsure, say so briefly and give your "
    "best attempt anyway."
)

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
    def _is_escalation_signal(text: str) -> bool:
        """True when *text* as a whole IS the escalation sentinel, matched loosely.

        Accepts "[ESCALATE]", "ESCALATE", "escalate.", "**[escalate]**", etc. —
        small local models are inconsistent about the exact brackets/casing.
        """
        return _escalation_alpha(text) == "ESCALATE"

    @classmethod
    def _needs_escalation(cls, response: str) -> bool:
        """True when the local model gave nothing usable / signalled it can't answer.

        Fires on an empty reply, on a bare sentinel, OR when the bracketed
        [ESCALATE] token appears anywhere — small models often append it to a
        chatty non-answer instead of replying with the token alone.
        """
        stripped = (response or "").strip()
        if not stripped:
            return True
        if _SENTINEL_RE.search(stripped):
            return True
        return cls._is_escalation_signal(stripped)

    @staticmethod
    def _classify_reply(message: str) -> str:
        """Interpret a reply to the escalation prompt: 'yes' / 'no' / 'other'.

        Robust to speech-to-text output: strips punctuation, lowercases, and
        collapses whitespace before matching ("Yes." / "Yeah!" / "Yes, please").
        """
        norm = re.sub(r"[^a-z\s]", " ", (message or "").lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        if not norm:
            return "other"
        words = norm.split()
        first = words[0]
        if norm in _AFFIRMATIVE_PHRASES or first in _AFFIRMATIVE_WORDS:
            return "yes"
        if norm in _NEGATIVE_PHRASES or first in _NEGATIVE_WORDS:
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

    async def _answer_locally_best_effort(self, session_id, user_id, question, history) -> tuple[str, str]:
        """Answer *question* on the local model without offering escalation.

        Used when the user declines escalation — they still want a best-effort
        local answer, not a dead end. Returns (response, backend).
        """
        local_backend = self._local_backend()
        system_prompt = self._build_system_prompt(question, session_id, user_id) + _BEST_EFFORT_INSTRUCTION
        messages = list(history) + [{"role": "user", "content": question}]
        try:
            response, backend = await self._local_answer(messages, system_prompt, local_backend)
        except Exception:
            logger.exception("Local best-effort answer failed")
            response, backend = "", local_backend
        # Strip any stray sentinel — we promised not to re-offer.
        response = _SENTINEL_RE.sub("", response or "").strip()
        if not response:
            response = "I'm afraid I can't do that one justice on my own just now, but there it is."
        return response, backend

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
            logger.info("Escalation reply %r classified as %s (session %s)",
                        user_message, decision, session_id)
            if decision == "yes":
                question = self._pending_escalation.pop(session_id)
                self.memory.add_message(session_id, "user", user_message)
                sys_prompt = self._build_system_prompt(question, session_id, user_id)
                return await self._answer_with_claude(session_id, user_id, question, sys_prompt)
            if decision == "no":
                question = self._pending_escalation.pop(session_id, None)
                self.memory.add_message(session_id, "user", user_message)
                if question:
                    response, backend = await self._answer_locally_best_effort(
                        session_id, user_id, question, history
                    )
                    return self._finalize(session_id, user_id, question, response, backend)
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
            logger.info("Escalation reply %r classified as %s (session %s)",
                        user_message, decision, session_id)
            if decision == "yes":
                question = self._pending_escalation.pop(session_id)
                sys_prompt = self._build_system_prompt(question, session_id, user_id)
                async for ev in self._stream_claude(session_id, user_id, question, sys_prompt):
                    yield ev
                return
            if decision == "no":
                question = self._pending_escalation.pop(session_id, None)
                if question:
                    response, backend = await self._answer_locally_best_effort(
                        session_id, user_id, question, history
                    )
                    self.memory.add_message(session_id, "assistant", response, llm_used=backend)
                    if self.semantic_memory:
                        self.semantic_memory.add(session_id, "user", question)
                        self.semantic_memory.add(session_id, "assistant", response)
                    if self.profile_extractor and self.profile_store:
                        self._spawn_profile_update(user_id, question, response)
                    yield {"session_id": session_id, "chunk": response, "backend": backend, "done": False}
                    yield {"session_id": session_id, "chunk": "", "backend": backend, "done": True}
                    return
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
            # Buffer the whole local reply before emitting. Small models are
            # inconsistent — they may prepend chatter to the [ESCALATE] token —
            # so we can only reliably decide "answer vs escalate" once complete.
            buffer = ""
            backend_used = local_backend
            failed = False

            try:
                async for chunk, backend in self.router.stream_chat(
                    history, force_backend=local_backend, system_prompt=local_system
                ):
                    backend_used = backend
                    buffer += chunk
            except Exception:
                logger.exception("Local streaming failed; offering escalation")
                failed = True

            complete = buffer.strip()

            # Empty, errored, or the model signalled it can't answer → offer Claude.
            if failed or self._needs_escalation(complete):
                self._pending_escalation[session_id] = user_message
                prompt = self._escalation_prompt()
                self.memory.add_message(session_id, "assistant", prompt, llm_used="victoria")
                logger.info("Offering escalation for session %s (local=%s)", session_id, local_backend)
                yield {"session_id": session_id, "chunk": prompt, "backend": "victoria", "done": False}
                yield {"session_id": session_id, "chunk": "", "backend": "victoria", "done": True}
                return

            yield {"session_id": session_id, "chunk": complete, "backend": backend_used, "done": False}
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
