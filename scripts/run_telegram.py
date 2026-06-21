#!/usr/bin/env python3
"""Start the Victoria Telegram bot (polling mode)."""
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
from victoria.interfaces.telegram_bot import VictoriaTelegramBot


def main():
    if not settings.telegram_bot_token:
        print("Error: TELEGRAM_BOT_TOKEN is not set in .env")
        sys.exit(1)

    memory = MemoryStore(db_path=settings.db_path)
    router = LLMRouter()
    manager = ConversationManager(memory=memory, llm_router=router)

    bot = VictoriaTelegramBot(manager=manager)
    app = bot.build_app()

    print("Victoria Telegram bot is live. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
