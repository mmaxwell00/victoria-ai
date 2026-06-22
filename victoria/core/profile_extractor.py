import json
import logging
import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from victoria.core.user_profile import UserProfile
    from victoria.core.llm_router import LLMRouter

logger = logging.getLogger(__name__)

# Patterns that signal the user wants Victoria to remember something
_MEMORY_PATTERNS = [
    r"(?:please\s+)?remember\s+that\s+(.+)",
    r"(?:please\s+)?don't\s+forget\s+(?:that\s+)?(.+)",
    r"(?:please\s+)?note\s+that\s+(.+)",
    r"(?:please\s+)?keep\s+in\s+mind\s+(?:that\s+)?(.+)",
    r"fyi[,:\s]+(.+)",
    r"just\s+so\s+you\s+know[,:\s]+(.+)",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _MEMORY_PATTERNS]

# LLM extraction only runs every N turns to avoid overhead
EXTRACT_EVERY_N_TURNS = 5


class ProfileExtractor:
    """Extracts and updates user preferences from conversation turns.

    detect_explicit_memory() — O(1) regex, called on every turn
    extract_from_turn()      — async LLM call, called every N turns in the background
    """

    def __init__(self, router: "LLMRouter"):
        self.router = router
        self._turn_counter: dict[str, int] = {}  # user_id → turn count

    # ------------------------------------------------------------------ #
    # Explicit memory detection (regex, synchronous)                       #
    # ------------------------------------------------------------------ #

    def detect_explicit_memory(self, message: str) -> Optional[str]:
        """Return the memory string if the message contains an explicit remember command.

        Examples:
          "remember that I prefer bullet points" → "I prefer bullet points"
          "don't forget that I'm based in Alabama" → "I'm based in Alabama"
          "what's the weather?" → None
        """
        msg = message.strip()
        for pattern in _COMPILED:
            m = pattern.match(msg)
            if m:
                memory = m.group(1).strip().rstrip(".")
                return memory if memory else None
        return None

    # ------------------------------------------------------------------ #
    # LLM-based style extraction (async, called in background)             #
    # ------------------------------------------------------------------ #

    def should_extract(self, user_id: str) -> bool:
        """Return True every EXTRACT_EVERY_N_TURNS turns per user."""
        count = self._turn_counter.get(user_id, 0) + 1
        self._turn_counter[user_id] = count
        return count % EXTRACT_EVERY_N_TURNS == 0

    async def extract_from_turn(
        self,
        profile: "UserProfile",
        user_message: str,
        assistant_response: str,
        user_id: str = "default",
    ) -> "UserProfile":
        """Analyse a conversation turn and return an updated profile.

        Uses a lightweight LLM call to extract implicit style signals.
        Returns the profile unchanged if nothing new is learned or on any error.
        Only runs every EXTRACT_EVERY_N_TURNS turns.
        """
        if not self.should_extract(user_id):
            return profile

        prompt = f"""Analyse this conversation turn and extract any clear signals about the user's communication preferences or style. Be conservative — only flag strong signals, not guesses.

User message: "{user_message}"
Assistant response: "{assistant_response}"

Current known preferences: {profile.preferences}
Current known topics: {profile.topics_of_interest}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "new_preferences": [],
  "new_topics": [],
  "style_note": null
}}

Guidelines:
- new_preferences: short phrases like "prefers bullet points", "wants concise answers", "likes code examples"
- new_topics: subject areas the user shows interest in, e.g. "machine learning", "home automation"
- style_note: one-line style observation to set as communication_style, or null if no clear signal
- Only include items clearly NEW relative to current known preferences/topics
- Return empty lists if nothing is clearly new"""

        try:
            messages = [{"role": "user", "content": prompt}]
            text, _ = await self.router.chat(messages)
            # Strip markdown code fences if present
            text = text.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:-1])
            data = json.loads(text)

            new_prefs = data.get("new_preferences") or []
            new_topics = data.get("new_topics") or []
            style_note = data.get("style_note")

            if not any([new_prefs, new_topics, style_note]):
                return profile

            from copy import deepcopy
            updated = deepcopy(profile)
            for p in new_prefs:
                if p and p not in updated.preferences:
                    updated.preferences.append(p)
            for t in new_topics:
                if t and t not in updated.topics_of_interest:
                    updated.topics_of_interest.append(t)
            if style_note and style_note != updated.communication_style:
                updated.communication_style = style_note

            return updated

        except Exception as exc:
            logger.debug("Profile extraction failed (non-critical): %s", exc)
            return profile
