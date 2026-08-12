"""Keep the local model resident so the first question after a lull is fast.

Docker Model Runner evicts an idle model after roughly five minutes. The reload
is paid by whoever asks the next question, and it dominates everything else in
the request: measured on this stack, **5.3s cold vs 0.36s warm** — a ~15x
difference, against a raw model call of ~0.15s. That reload is exactly what
reads as Victoria "pausing" before answering; you ask, wait, ask again, and the
second reply is instant because the first one loaded the model.

There is no TTL knob to turn — neither `docker model` nor
`docker desktop enable model-runner` exposes one (only `--cors` / `--tcp`) — so
the fix lives here: a tiny completion on a timer keeps the model loaded.

Deliberately unobtrusive:
- one token, no tools, no history — the point is to touch the model, not to think;
- never raises. A warmer that crashes the app (or spams the log while the Model
  Runner is down) is worse than a slow first question;
- opt-out via `model_keepalive_seconds = 0`, because staying warm costs RAM
  (~4.4 GB for qwen2.5) — that is the user's call, not ours.
"""
import asyncio
import logging

import httpx

from victoria.config import settings

logger = logging.getLogger(__name__)


async def _ping(client: httpx.AsyncClient) -> bool:
    """One minimal completion. True if the model answered."""
    r = await client.post(
        f"{settings.model_runner_url.rstrip('/')}/chat/completions",
        json={
            "model": settings.model_runner_model,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 1,
            "stream": False,
        },
    )
    r.raise_for_status()
    return True


async def keep_model_warm() -> None:
    """Ping the local model every `model_keepalive_seconds` until cancelled."""
    interval = settings.model_keepalive_seconds
    if interval <= 0:
        logger.info("Model keep-alive disabled (model_keepalive_seconds=0)")
        return

    logger.info("Model keep-alive every %ss (%s)", interval, settings.model_runner_model)
    # Log only on state CHANGE, so a healthy warmer stays silent in the log and a
    # persistent outage doesn't emit an entry every few minutes.
    was_ok: bool | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            try:
                await _ping(client)
                if was_ok is False:
                    logger.info("Model keep-alive recovered")
                was_ok = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if was_ok is not False:
                    logger.warning(
                        "Model keep-alive ping failed (%s); the next question will "
                        "pay the model reload. Retrying.", exc
                    )
                was_ok = False
            await asyncio.sleep(interval)
