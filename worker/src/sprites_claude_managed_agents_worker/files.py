"""Seed files declared in the session's create metadata.

The dispatcher passes a JSON mapping of mount paths to https URLs in
``CMA_SESSION_FILES``, before handing the session to the SDK. Downloads only
happen the first time the worker boots in a Sprite. A file that fails to
download is logged and skipped.
"""

import json
import logging
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

FILES_ENV = "CMA_SESSION_FILES"
DOWNLOAD_TIMEOUT = 60.0


def requested() -> dict[str, str]:
    """The files the dispatcher asked for, keyed by mount path."""
    return json.loads(os.environ.get(FILES_ENV) or "{}")


async def seed(files: Mapping[str, str]) -> None:
    """Download ``files`` into the Sprite, best effort."""
    if not files:
        return
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=DOWNLOAD_TIMEOUT
    ) as client:
        await download_all(client, files)


async def download_all(client: httpx.AsyncClient, files: Mapping[str, str]) -> None:
    for path, url in files.items():
        dest = Path(path)
        if dest.exists():
            logger.info("%s already exists, skipping %s", dest, url)
            continue
        try:
            await _download(client, url, dest)
        except (httpx.HTTPError, OSError) as e:
            logger.error("failed to download %s: %s: %s", url, type(e).__name__, e)
        else:
            logger.info("downloaded %s -> %s", url, dest)


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    try:
        with os.fdopen(fd, "wb") as out:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                # Redirects are followed, but only ever to another https URL.
                if response.url.scheme != "https":
                    raise httpx.HTTPError(f"{url} redirected off https")
                async for chunk in response.aiter_bytes():
                    out.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
