import asyncio
import numpy as np
from typing import Optional
import logging

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 0.1        # seconds per read chunk
SILENCE_THRESHOLD = 500     # RMS amplitude below this = silence
SILENCE_DURATION = 1.5      # seconds of silence to stop recording
MIN_SPEECH_DURATION = 0.5   # minimum non-silent audio before we'll stop
MAX_RECORDING_DURATION = 30 # hard cap in seconds


async def record_until_silence(
    sample_rate: int = SAMPLE_RATE,
    silence_threshold: int = SILENCE_THRESHOLD,
    silence_duration: float = SILENCE_DURATION,
    max_duration: float = MAX_RECORDING_DURATION,
) -> Optional[np.ndarray]:
    """Record from microphone until the user stops speaking.

    Returns int16 numpy array suitable for Whisper, or None if only silence captured.
    """
    loop = asyncio.get_running_loop()

    def _blocking_record() -> Optional[np.ndarray]:
        import sounddevice as sd  # lazy import — avoids PortAudio at module load time

        chunk_samples = int(sample_rate * CHUNK_DURATION)
        max_chunks = int(max_duration / CHUNK_DURATION)

        all_chunks = []
        silence_seconds = 0.0
        speech_seconds = 0.0

        with sd.InputStream(samplerate=sample_rate, channels=CHANNELS, dtype='int16',
                            blocksize=chunk_samples) as stream:
            for _ in range(max_chunks):
                chunk, _ = stream.read(chunk_samples)
                chunk_flat = chunk.flatten()
                all_chunks.append(chunk_flat.copy())

                rms = np.sqrt(np.mean(chunk_flat.astype(np.float32) ** 2))

                if rms < silence_threshold:
                    silence_seconds += CHUNK_DURATION
                else:
                    silence_seconds = 0.0
                    speech_seconds += CHUNK_DURATION

                # Only stop on silence after minimum speech is captured
                if (silence_seconds >= silence_duration
                        and speech_seconds >= MIN_SPEECH_DURATION):
                    break

        # Return None if we barely captured any speech
        if speech_seconds < MIN_SPEECH_DURATION / 2:
            logger.debug("record_until_silence: only silence captured (speech=%.2fs)", speech_seconds)
            return None

        audio = np.concatenate(all_chunks)
        logger.debug(
            "record_until_silence: recorded %.2fs total, %.2fs speech",
            len(audio) / sample_rate,
            speech_seconds,
        )
        return audio

    return await loop.run_in_executor(None, _blocking_record)
