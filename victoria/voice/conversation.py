import asyncio
import logging
import os
import tempfile
import uuid
import numpy as np
from victoria.config import settings
from victoria.core.conversation import ConversationManager
from victoria.voice.tts.base import TTSEngine
from victoria.voice.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)

GOODBYE_PHRASES = frozenset(["goodbye", "bye", "that's all", "that will be all", "farewell", "see you"])


class VoiceConversation:
    def __init__(self, manager: ConversationManager, tts: TTSEngine, detector: WakeWordDetector):
        self.manager = manager
        self.tts = tts
        self.detector = detector

    async def run(self) -> None:
        """Main loop — runs until KeyboardInterrupt."""
        print("Victoria voice interface ready. Say 'Hello Victoria' to begin.")
        while True:
            print("\n[Waiting for wake word…]")
            await self.detector.wait_for_wake_word()
            print("[Wake word detected!]")
            await self._run_session()

    async def _run_session(self) -> None:
        """One conversation session — continues until timeout or goodbye."""
        session_id = str(uuid.uuid4())
        await self.tts.speak("Yes?")

        while True:
            try:
                should_continue = await asyncio.wait_for(
                    self._conversation_turn(session_id),
                    timeout=settings.voice_session_timeout,
                )
                if not should_continue:
                    break
            except asyncio.TimeoutError:
                print("[Session timeout — returning to wake word mode]")
                await self.tts.speak("I'll be here if you need me.")
                break

    async def _conversation_turn(self, session_id: str) -> bool:
        """One turn: record → transcribe → respond → speak.
        Returns True to continue session, False to end it.
        """
        print("[Listening…]")
        from victoria.voice.audio import record_until_silence
        audio = await record_until_silence()

        if audio is None:
            await self.tts.speak("I didn't quite catch that.")
            return True

        # Save to temp WAV for Whisper
        tmp_path = await _save_audio_to_wav(audio)
        try:
            from victoria.core.transcription import transcribe_audio
            print("[Transcribing…]")
            text = await transcribe_audio(tmp_path)
        finally:
            os.unlink(tmp_path)

        if not text.strip():
            await self.tts.speak("I didn't catch that — could you repeat?")
            return True

        print(f"[You said]: {text}")

        # Check for goodbye
        if any(phrase in text.lower() for phrase in GOODBYE_PHRASES):
            await self.tts.speak("Cheerio! Do call again.")
            return False

        # Get LLM response
        print("[Thinking…]")
        result = await self.manager.chat(
            user_message=text,
            session_id=session_id,
            user_id="voice",
            channel="voice",
        )
        response = result["response"]
        print(f"[Victoria ({result['backend']})]: {response}")

        print("[Speaking…]")
        await self.tts.speak(response)
        return True


async def _save_audio_to_wav(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Save numpy int16 array to a temp WAV file. Caller must delete."""
    import wave
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())
    return tmp.name
