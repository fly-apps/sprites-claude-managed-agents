"""Webhook-driven Sprite dispatcher.

Anthropic sends a ``session.status_run_started`` webhook when work is waiting in
the queue. We use this as a wake-up signal to start polling until we've drained
the queue. This both recovers any missed deliveries and allows the the Fly
machine to scale to zero when not in use.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import cache

import anthropic
from anthropic.types.beta import UnwrapWebhookEvent
from fastapi import FastAPI, HTTPException, Request
from standardwebhooks import WebhookVerificationError
from starlette.datastructures import Headers

from .config import get_settings
from .sandbox import spawn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Raise config failures before starting the server.
    get_settings()
    yield


app = FastAPI(lifespan=lifespan)


@cache
def _client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    return anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_environment_key,
        base_url=settings.anthropic_base_url,
        webhook_key=settings.anthropic_webhook_secret,
    )


@dataclass
class SpawnResult:
    session_id: str
    work_id: str
    sprite: str | None = None
    error: str | None = None


def _verify_webhook(raw: bytes, headers: Headers) -> UnwrapWebhookEvent:
    try:
        return _client().beta.webhooks.unwrap(raw.decode(), headers=headers)
    # KeyError and ValueError cover missing or malformed headers, which would
    # otherwise escape as a 500 instead of a 401.
    except (WebhookVerificationError, KeyError, ValueError) as e:
        logger.info("rejected webhook delivery: %s: %s", type(e).__name__, e)
        raise HTTPException(
            status_code=401, detail="signature verification failed"
        ) from None


async def _drain_work() -> list[SpawnResult]:
    """Drain the work queue, spawning a Sprite worker per session."""
    settings = get_settings()
    results: list[SpawnResult] = []
    async for work in _client().beta.environments.work.poller(
        environment_id=settings.anthropic_environment_id,
        environment_key=settings.anthropic_environment_key,
        block_ms=None,
        reclaim_older_than_ms=10_000,  # Sprites should be ready well before 10s
        drain=True,
        auto_stop=False,
    ):
        if work.data.type != "session":
            logger.info("skipping work=%s type=%s", work.id, work.data.type)
            continue
        result = SpawnResult(session_id=work.data.id, work_id=work.id)
        try:
            # Move the sync Sprites SDK off-thread.
            result.sprite = await asyncio.to_thread(
                spawn, work.data.id, work_id=work.id
            )
            logger.info(
                "work=%s session=%s -> %s",
                work.id,
                result.session_id,
                result.sprite,
            )
        except Exception:
            logger.exception("spawn failed for work=%s", work.id)
            result.error = "spawn failed"
        results.append(result)
    return results


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/")
async def webhook(request: Request) -> dict[str, object]:
    event = _verify_webhook(await request.body(), request.headers)
    if event.data.type != "session.status_run_started":
        return {"status": "ignored", "event_type": event.data.type}

    results = await _drain_work()
    # If nothing was dispatched, fail the delivery. Anthropic will call our
    # webhook again to retry.
    if results and all(r.error for r in results):
        raise HTTPException(status_code=500, detail="all spawns failed")
    return {"status": "ok", "spawned": results}
