"""Sprite lifecycle management for Claude sessions.

One Sprite per session, named after the session id. ``spawn`` installs the
worker into the Sprite and execs the worker with the session's credentials. The
worker daemonizes itself so it outlives the exec request, and holds a
Sprite-wide lock, so spawning is idempotent.
"""

import re
from functools import cache
from importlib.metadata import version

from sprites import SpritesClient
from sprites.exceptions import SpriteError
from sprites.sprite import Sprite

from .config import get_settings

WORKER_MODULE = "sprites_claude_managed_agents_worker.main"
WORKDIR = "/workspace"

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


def spawn(session_id: str, *, work_id: str) -> str:
    """Create (or reuse) the session's Sprite and start the worker in it."""
    settings = get_settings()
    name = sprite_name(session_id)
    try:
        sprite = _client().create_sprite(name, wait_for_capacity=True)
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
