"""Sprite lifecycle management for Claude sessions.

One Sprite per session, named after the session id. ``spawn`` installs the
worker into the Sprite and execs the worker with the session's credentials. The
worker daemonizes itself so it outlives the exec request, and holds a
Sprite-wide lock, so spawning is idempotent.

A session can ask for Sprite labels and seed files through its create
metadata. See ``sprite_labels`` and ``sprite_files`` for the formats. Metadata
too large for the API's limits can be named by url. See ``resolve_metadata``.
"""

import json
import posixpath
import re
from collections.abc import Mapping
from functools import cache
from importlib.metadata import version
from urllib.parse import urlsplit

import httpx
from sprites import SpritesClient
from sprites.exceptions import SpriteError
from sprites.sprite import Sprite

from .config import get_settings

WORKER_MODULE = "sprites_claude_managed_agents_worker.main"
WORKDIR = "/workspace"

# Where seed files land, matching the hosted sandbox's uploads directory.
UPLOADS_DIR = "/mnt/session/uploads"

# The files the worker downloads on first boot, as JSON. See ``sprite_files``.
FILES_ENV = "CMA_SESSION_FILES"

METADATA_TIMEOUT = 30.0
METADATA_MAX_BYTES = 1 << 20

# Where the worker closure is unpacked, and where the pip fallback installs a
# fresh copy. DEPS_DIR precedes VENDOR_DIR on PYTHONPATH so fallback installs
# shadow the closure's wheels.
SCRATCH_DIR = "/home/sprite/.cma-worker"
VENDOR_DIR = SCRATCH_DIR + "/vendor"
FALLBACK_DIR = SCRATCH_DIR + "/deps"

# Unpack through a temp dir + rename so concurrent spawns race safely, then
# verify the closure's wheels actually import on this runtime. If the Sprite
# runtime's Python has drifted from the build, fall back to pip-installing fresh
# dependencies.
_INSTALL_SCRIPT = f"""
mkdir -p {SCRATCH_DIR} || exit 1
if [ ! -d {VENDOR_DIR} ]; then
    tmp=$(mktemp -d {VENDOR_DIR}.XXXXXX) || exit 1
    tar -xzf {SCRATCH_DIR}/vendor.tar.gz -C "$tmp" || exit 1
    mv -T "$tmp" {VENDOR_DIR} || rm -rf "$tmp"
fi
[ -d {VENDOR_DIR} ] || exit 1
rm -f {SCRATCH_DIR}/vendor.tar.gz
mkdir -p {WORKDIR} || exit 1
PYTHONPATH={VENDOR_DIR} /usr/bin/python3 -c 'import {WORKER_MODULE}' ||
    /usr/bin/python3 -m pip install --quiet --upgrade --target {FALLBACK_DIR} \
        {" ".join(f"{pkg}=={version(pkg)}" for pkg in ("anthropic", "httpx"))}
"""


@cache
def _client() -> SpritesClient:
    settings = get_settings()
    return SpritesClient(
        token=settings.sprite_token,
        base_url=settings.sprites_api_url,
    )


def sprite_name(session_id: str) -> str:
    """A valid Sprite name derived from the session id."""
    slug = re.sub(r"[^a-z0-9]+", "-", session_id.lower()).strip("-")[:40]
    if not slug:
        raise ValueError(f"cannot derive a sprite name from {session_id!r}")
    return f"claude-agent-{slug}"


def resolve_metadata(
    metadata: Mapping[str, str], *, client: httpx.Client | None = None
) -> dict[str, str]:
    """A session's create metadata, expanded with a hosted payload.

    The API caps how much metadata a session can carry, so the full metadata
    can be hosted as a JSON object and named by an https url:

        {"metadata": "https://example.com/session.json"}
    """
    url = metadata.get("metadata", "")
    if not url:
        return dict(metadata)
    if urlsplit(url.strip()).scheme != "https":
        raise ValueError(f"metadata url {url!r} is not https")

    if client is None:
        with httpx.Client(
            follow_redirects=True, timeout=METADATA_TIMEOUT
        ) as http_client:
            fetched = _fetch_metadata(http_client, url)
    else:
        fetched = _fetch_metadata(client, url)
    return fetched


def _fetch_metadata(client: httpx.Client, url: str) -> dict[str, str]:
    """Download the hosted metadata object at ``url``."""
    body = bytearray()
    with client.stream("GET", url) as response:
        response.raise_for_status()
        if response.url.scheme != "https":
            raise ValueError(f"metadata url {url!r} redirected off https")
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > METADATA_MAX_BYTES:
                raise ValueError(
                    f"metadata at {url!r} is larger than {METADATA_MAX_BYTES} bytes"
                )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"metadata at {url!r} is not valid JSON: {e}") from None
    if not isinstance(payload, dict):
        raise ValueError(f"metadata at {url!r} must be a JSON object")
    if "metadata" in payload:
        raise ValueError(f"metadata at {url!r} cannot name another metadata URL")
    return {
        key: value if isinstance(value, str) else json.dumps(value)
        for key, value in payload.items()
    }


def sprite_labels(metadata: Mapping[str, str]) -> list[str]:
    """The Sprite labels requested by a session's create metadata."""
    labels: list[str] = []
    for value in metadata.get("labels", "").split(","):
        label = value.strip()
        if not label:
            continue
        if label not in labels:
            labels.append(label)
    return labels


def sprite_files(metadata: Mapping[str, str]) -> dict[str, str]:
    """The files requested by a session's create metadata.

    Metadata values are strings, so the mapping rides along as a JSON object of
    mount path to https URL:

        {"files": "{\"/data.csv\": \"https://example.com/data.csv\"}"}

    Mount paths are absolute, but rooted under ``UPLOADS_DIR``, so ``/data.csv``
    lands at ``/mnt/session/uploads/data.csv``. The worker downloads them the
    first time it boots in the Sprite, creating parent directories as it goes.
    """
    raw = metadata.get("files", "").strip()
    if not raw:
        return {}
    try:
        requested = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"metadata 'files' is not valid JSON: {e}") from None
    if not isinstance(requested, dict):
        raise ValueError("metadata 'files' must be a JSON object of path to url")

    files: dict[str, str] = {}
    for path, url in requested.items():
        if not isinstance(url, str):
            raise ValueError(f"file url for {path!r} must be a string")
        if urlsplit(url).scheme != "https":
            raise ValueError(f"file url {url!r} is not https")
        files[_upload_path(path)] = url
    return files


def _upload_path(path: str) -> str:
    """Root a mount path under UPLOADS_DIR, refusing to escape it."""
    if posixpath.basename(path) in ("", "."):
        raise ValueError(f"mount path {path!r} does not name a file")
    rooted = posixpath.normpath(posixpath.join(UPLOADS_DIR, path.lstrip("/")))
    if not rooted.startswith(f"{UPLOADS_DIR}/"):
        raise ValueError(f"mount path {path!r} is outside of {UPLOADS_DIR}")
    return rooted


def spawn(
    session_id: str, *, work_id: str, metadata: Mapping[str, str] | None = None
) -> str:
    """Create (or reuse) the session's Sprite and start the worker in it."""
    settings = get_settings()
    name = sprite_name(session_id)
    # Parse before creating the Sprite so a bad request fails without leaving
    # one behind.
    resolved = resolve_metadata(metadata or {})
    labels = sprite_labels(resolved)
    files = sprite_files(resolved)
    try:
        sprite = _client().create_sprite(
            name, labels=labels or None, wait_for_capacity=True
        )
    except SpriteError as e:
        # sprites-py only reports the HTTP status in the message. 409 means the
        # Sprite already exists and we can reuse it.
        if "(status 409)" not in str(e):
            raise
        sprite = _client().sprite(name)

    _install_worker(sprite)

    # The credentials ride on the exec's environment, so they never touch the
    # Sprite's disk. This returns as soon as the worker starts.
    sprite.run(
        "/usr/bin/python3",
        "-m",
        WORKER_MODULE,
        env={
            "PYTHONPATH": f"{FALLBACK_DIR}:{VENDOR_DIR}",
            "ANTHROPIC_BASE_URL": settings.anthropic_base_url,
            "ANTHROPIC_ENVIRONMENT_KEY": settings.anthropic_environment_key,
            "ANTHROPIC_ENVIRONMENT_ID": settings.anthropic_environment_id,
            "ANTHROPIC_SESSION_ID": session_id,
            "ANTHROPIC_WORK_ID": work_id,
            FILES_ENV: json.dumps(files),
        },
        check=True,
    )
    return name


def _install_worker(sprite: Sprite) -> None:
    """Install the worker closure into the Sprite.

    To speed up cold starts, the closure (worker package plus its dependency
    tree) is prebuilt in the Docker image. Attempt to push and unpack it,
    falling back to ``pip install`` if that fails.

    The probe catches if the closure is either missing or broken. In both cases,
    the install script repairs it.
    """
    probe = sprite.run(
        "/usr/bin/python3",
        "-c",
        f"import {WORKER_MODULE}",
        env={"PYTHONPATH": f"{FALLBACK_DIR}:{VENDOR_DIR}"},
    )
    if probe.returncode == 0:
        return
    (sprite.filesystem() / SCRATCH_DIR / "vendor.tar.gz").write_bytes(
        get_settings().vendor_tar_path.read_bytes(), mode=0o600
    )
    sprite.run("bash", "-c", _INSTALL_SCRIPT, check=True)
