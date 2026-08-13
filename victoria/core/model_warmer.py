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


def _warm_prefix() -> tuple[str, list[dict]]:
    """Victoria's REAL stable prefix — the system prompt and tool schemas.

    The ping must carry this, not a bare "ok". llama.cpp caches the prompt PREFIX,
    and a ping with a *different* prompt evicts the cached one — so the original
    version of this warmer kept the model resident while actively destroying the
    thing that made answers fast (measured: a real question 2.5s, then a bare-"ok"
    ping, then the next question 5.1s). Pinging with the real prefix keeps both the
    model AND the cache warm, so the next question skips ~2,250 tokens of tool-schema
    prefill.
    """
    from victoria.config import VICTORIA_SYSTEM_PROMPT, ESCALATION_INSTRUCTION
    system = VICTORIA_SYSTEM_PROMPT + ESCALATION_INSTRUCTION
    tools: list[dict] = []
    try:
        from victoria.tools.registry import registry
        tools = registry.get_ollama_tools()
    except Exception:      # tools are a bonus here, never a reason to fail
        pass
    return system, tools


async def _ping(client: httpx.AsyncClient) -> bool:
    """One minimal completion over the real prefix. True if the model answered."""
    system, tools = _warm_prefix()
    payload = {
        "model": settings.model_runner_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "ok"},
        ],
        "max_tokens": 1,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    r = await client.post(
        f"{settings.model_runner_url.rstrip('/')}/chat/completions", json=payload
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
