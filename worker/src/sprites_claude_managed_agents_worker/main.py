"""The session worker.

The dispatcher execs this module when a session has queued work. It takes a
Sprite-wide lock (making repeat spawns a no-op), daemonizes to outlive the
dispatcher's exec request, and hands the session to the SDK. ``handle_item()``
runs the tool loop for the claimed work item and force-stops it on exit. The
worker exits about a minute after the session goes idle with ``end_turn``.

Sprites pause after a short idle window, and a session only makes outbound API
calls. To prevent the Sprite from automatically suspending, the worker registers
a Sprite keep-alive Task, and refreshes it for as long as the session runs. If
the worker crashes, the Task expires on its own.
"""

import asyncio
import fcntl
import logging
import os
import sys
from typing import IO

from anthropic import AsyncAnthropic

from . import tasks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKDIR = "/workspace"
LOCK_PATH = "/run/cma-worker.lock"
LOG_PATH = "/var/log/cma-worker.log"

TASK_NAME = "cma-worker"
TASK_EXPIRE = "5m"
TASK_HEARTBEAT_SECONDS = 60.0

_lock: IO[str] | None = None


def _acquire_session_lock() -> bool:
    global _lock
    _lock = open(LOCK_PATH, "w")  # noqa: SIM115 (Held for the process lifetime)
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _daemonize() -> None:
    """Detach into a new session and redirect stdio to LOG_PATH.

    The daemon inherits the lock file's descriptor, which keeps the lock held
    after both parents exit.
    """
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    log = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, sys.stdin.fileno())
    os.dup2(log, sys.stdout.fileno())
    os.dup2(log, sys.stderr.fileno())
    os.close(null)
    os.close(log)


async def _run_session() -> None:
    environment_key = os.environ["ANTHROPIC_ENVIRONMENT_KEY"]
    async with (
        tasks.keepalive(TASK_NAME, TASK_EXPIRE, TASK_HEARTBEAT_SECONDS),
        AsyncAnthropic(auth_token=environment_key) as client,
    ):
        await client.beta.environments.work.worker(
            environment_key=environment_key,
            workdir=WORKDIR,
            unrestricted_paths=True,
        ).handle_item()


def main() -> None:
    if not _acquire_session_lock():
        print("a runner is already active in this Sprite. exiting...")
        return
    _daemonize()
    asyncio.run(_run_session())


if __name__ == "__main__":
    main()
