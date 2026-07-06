import os
import tempfile
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from victoria.config import settings
from victoria.core.conversation import ConversationManager

logger = logging.getLogger(__name__)

CHANNEL = "telegram"


def _session_id(user_id: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("session_id", f"tg-{user_id}")


class VictoriaTelegramBot:
    def __init__(self, manager: ConversationManager):
        self.manager = manager

    # ------------------------------------------------------------------ #
    # Commands                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = update.effective_user.first_name or "darling"
        await update.message.reply_text(
            f"Hello, {name}! I'm Victoria — your personal AI assistant. "
            "Brilliant, British, and entirely at your service. "
            "What can I do for you today?"
        )

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        import time
        user_id = str(update.effective_user.id)
        context.user_data["session_id"] = f"tg-{user_id}-{int(time.time())}"
        context.user_data.pop("force_backend", None)
        await update.message.reply_text(
            "Right, fresh start it is! Consider the slate wiped clean. "
            "What's on your mind?"
        )

    async def cmd_remember(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /remember I prefer concise answers")
            return
        memory = " ".join(context.args)
        user_id = str(update.effective_user.id)
        if self.manager.profile_store:
            self.manager.profile_store.add_memory(user_id, memory)
            # No parse mode — user text can contain Markdown metacharacters
            # that would make Telegram reject the message.
            await update.message.reply_text(f'Noted and filed away: "{memory}"')
        else:
            await update.message.reply_text("Memory storage isn't available at the moment, darling.")

    async def cmd_forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /forget <exact memory text>")
            return
        memory = " ".join(context.args)
        user_id = str(update.effective_user.id)
        if self.manager.profile_store:
            removed = self.manager.profile_store.forget_memory(user_id, memory)
            if removed:
                await update.message.reply_text("Consider it forgotten, darling.")
            else:
                await update.message.reply_text(
                    "I don't seem to have that on record. Use /profile to see what I know."
                )
        else:
            await update.message.reply_text("Memory storage isn't available at the moment.")

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if self.manager.profile_store:
            profile = self.manager.profile_store.get(user_id)
            context_text = profile.to_system_context()
            if context_text:
                await update.message.reply_text(context_text)
            else:
                await update.message.reply_text(
                    "I don't know much about you yet — chat with me for a bit and I'll start learning your style!"
                )
        else:
            await update.message.reply_text("Profile storage isn't available at the moment.")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "*Victoria — Command Reference*\n\n"
            "/start — Wake me up\n"
            "/new — Start a fresh conversation\n"
            "/remember <text> — Tell me something to remember\n"
            "/forget <text> — Make me forget something\n"
            "/profile — See what I know about you\n"
            "/backend ollama|claude|docker — Switch AI brain\n"
            "/help — This list\n\n"
            "Send voice messages and I'll transcribe them automatically.",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_backend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        allowed = ("ollama", "claude", "docker")
        if not context.args or context.args[0] not in allowed:
            await update.message.reply_text(
                f"Usage: /backend {'|'.join(allowed)}"
            )
            return
        backend = context.args[0]
        context.user_data["force_backend"] = backend
        await update.message.reply_text(
            f"Switching to *{backend}*. Consider it done, darling.",
            parse_mode=ParseMode.MARKDOWN,
        )

    # ------------------------------------------------------------------ #
    # Message handlers                                                     #
    # ------------------------------------------------------------------ #

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        session_id = _session_id(user_id, context)
        force_backend = context.user_data.get("force_backend")

        thinking = await update.message.reply_text("…")
        try:
            result = await self.manager.chat(
                user_message=update.message.text,
                session_id=session_id,
                user_id=user_id,
                channel=CHANNEL,
                force_backend=force_backend,
            )
            await thinking.edit_text(result["response"])
        except Exception as exc:
            logger.exception("Chat error")
            await thinking.edit_text(
                "Terribly sorry, something went sideways on my end. Do try again."
            )

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from victoria.core.transcription import transcribe_audio

        user_id = str(update.effective_user.id)
        session_id = _session_id(user_id, context)

        status = await update.message.reply_text("Transcribing your voice note…")

        tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        tmp.close()
        try:
            voice_file = await context.bot.get_file(update.message.voice.file_id)
            await voice_file.download_to_drive(tmp.name)
            transcription = await transcribe_audio(tmp.name)
        except Exception:
            logger.exception("Transcription error")
            await status.edit_text(
                "I couldn't quite make that out, darling. Could you try again?"
            )
            return
        finally:
            os.unlink(tmp.name)

        if not transcription:
            await status.edit_text(
                "Hmm, that came through as silence. Try again?"
            )
            return

        # Plain text throughout — transcriptions and model output can contain
        # Markdown metacharacters that make Telegram reject the message.
        await status.edit_text(f"🎤 {transcription}\n\n…")

        try:
            result = await self.manager.chat(
                user_message=transcription,
                session_id=session_id,
                user_id=user_id,
                channel=CHANNEL,
            )
            await status.edit_text(f"🎤 {transcription}\n\n{result['response']}")
        except Exception:
            logger.exception("Chat error after transcription")
            await status.edit_text(
                f"🎤 {transcription}\n\n"
                "Transcribed, but hit a snag generating a response. Sorry!"
            )

    # ------------------------------------------------------------------ #
    # App builder                                                          #
    # ------------------------------------------------------------------ #

    def build_app(self) -> Application:
        app = Application.builder().token(settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("backend", self.cmd_backend))
        app.add_handler(CommandHandler("remember", self.cmd_remember))
        app.add_handler(CommandHandler("forget", self.cmd_forget))
        app.add_handler(CommandHandler("profile", self.cmd_profile))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
        return app
