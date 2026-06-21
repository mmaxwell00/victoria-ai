#!/usr/bin/env python3
"""Quick CLI to chat with Victoria without opening a browser."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from victoria.core.memory import MemoryStore
from victoria.core.llm_router import LLMRouter
from victoria.core.conversation import ConversationManager
from victoria.config import settings
import uuid


async def main():
    memory = MemoryStore(db_path=settings.db_path)
    router = LLMRouter()
    manager = ConversationManager(memory=memory, llm_router=router)
    session_id = str(uuid.uuid4())

    print("Victoria AI — Sprint 1 CLI")
    print(f"Backend: {settings.default_llm}  |  Session: {session_id[:8]}")
    print("Type 'quit' to exit, 'claude' or 'ollama' to force a backend.\n")

    force_backend = None

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCheerio!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("Cheerio!")
            break
        if user_input.lower() in ("claude", "ollama"):
            force_backend = user_input.lower()
            print(f"[Forcing {force_backend} for next message]")
            continue

        print("Victoria: ", end="", flush=True)
        async for event in manager.stream_chat(
            user_message=user_input,
            session_id=session_id,
            force_backend=force_backend,
        ):
            if not event["done"]:
                print(event["chunk"], end="", flush=True)
            else:
                print(f"\n[{event['backend']}]\n")
        force_backend = None


if __name__ == "__main__":
    asyncio.run(main())
