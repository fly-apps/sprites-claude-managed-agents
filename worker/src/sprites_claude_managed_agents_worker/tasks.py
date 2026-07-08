"""Client for the Sprite's Task API.

A Task is a hold that keeps a Sprite alive, even if it would normally stop due
to inactivity.

For more information, see: https://docs.sprites.dev/keeping-sprites-running
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

SPRITE_API_SOCKET = "/.sprite/api.sock"

# We dial the Sprite API socket directly here. It's equally possible to interact
# with tasks by shelling out to ``sprite-env``.
_client = httpx.AsyncClient(
    transport=httpx.AsyncHTTPTransport(uds=SPRITE_API_SOCKET),
    base_url="http://sprite",
    timeout=10,
)


async def call(method: str, path: str, body: dict | None = None) -> None:
    """Call the Task API over the Unix socket, best effort."""
    try:
        await _client.request(method, path, json=body)
    except (httpx.HTTPError, OSError) as e:
        # The keep-alive Task is only a resilience mechanism. At worst, a failed
        # call pauses the Sprite early and the dispatcher needs to wake it back
        # up. Log the error instead of propagating it.
        logger.debug("task api %s %s failed: %s", method, path, e)


async def heartbeat(name: str, expire: str, interval: float) -> None:
    """Keep Task ``name`` alive until cancelled by the caller."""
    while True:
        await call("PUT", f"/v1/tasks/{name}", {"expire": expire})
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def keepalive(name: str, expire: str, interval: float) -> AsyncGenerator[None]:
    """Hold Task ``name`` alive for the duration of the block."""
    hb = asyncio.create_task(heartbeat(name, expire, interval))
    try:
        yield
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            # Await the heartbeat so a mid-flight PUT can't resurrect the task.
            await hb
        await call("DELETE", f"/v1/tasks/{name}")
