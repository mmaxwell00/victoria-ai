#!/usr/bin/env python3
"""Start Victoria's voice interface."""
import asyncio
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)

from victoria.config import settings
from victoria.core.memory import MemoryStore
from victoria.core.llm_router import LLMRouter
from victoria.core.conversation import ConversationManager
from victoria.voice.tts.factory import get_tts_engine
from victoria.voice.wake_word import WakeWordDetector
from victoria.voice.conversation import VoiceConversation


async def main():
    print("=" * 50)
    print("  Victoria AI — Voice Interface")
    print(f"  TTS: {settings.tts_engine}  |  LLM: {settings.default_llm}")
    print(f"  Wake word: '{settings.wake_word}'")
    print("  Press Ctrl+C to quit.")
    print("=" * 50)

    memory = MemoryStore(db_path=settings.db_path)
    router = LLMRouter()
    manager = ConversationManager(memory=memory, router=router)
    tts = get_tts_engine()
    detector = WakeWordDetector()
    voice = VoiceConversation(manager=manager, tts=tts, detector=detector)

    try:
        await voice.run()
    except KeyboardInterrupt:
        print("\nCheerio!")
    finally:
        await tts.close()


if __name__ == "__main__":
    asyncio.run(main())
