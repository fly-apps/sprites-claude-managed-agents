import asyncio
import base64
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

TEST_ENV = {
    "ANTHROPIC_ENVIRONMENT_ID": "env_test",
    "ANTHROPIC_ENVIRONMENT_KEY": "sk-ant-test",
    "ANTHROPIC_WEBHOOK_SECRET": "whsec_" + base64.b64encode(b"t" * 32).decode(),
    "SPRITE_TOKEN": "sprite-test",
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)

    # Create a dummy vendor tarball so that the app starts.
    vendor_tar = tmp_path / "vendor.tar.gz"
    vendor_tar.touch()
    monkeypatch.setenv("VENDOR_TAR_PATH", str(vendor_tar))

    from sprites_claude_managed_agents_dispatch import config, main

    # Settings and clients are cached per process. Reset so this test's env
    # is correct.
    config.get_settings.cache_clear()
    main._client.cache_clear()
    with TestClient(main.app) as test_client:
        yield test_client
    config.get_settings.cache_clear()
    main._client.cache_clear()


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_rejects_delivery_without_signature_headers(client):
    response = client.post("/", content=b"{}")
    assert response.status_code == 401


def test_webhook_rejects_delivery_with_bad_signature(client):
    response = client.post(
        "/",
        content=b"{}",
        headers={
            "webhook-id": "msg_test",
            "webhook-timestamp": str(int(time.time())),
            "webhook-signature": "v1," + base64.b64encode(b"bogus").decode(),
        },
    )
    assert response.status_code == 401


def work_item(
    work_id, *, state="starting", acked_ago=timedelta(minutes=5), heartbeat=None
):
    return SimpleNamespace(
        id=work_id,
        data=SimpleNamespace(id=f"session_{work_id}", type="session"),
        state=state,
        acknowledged_at=(datetime.now(UTC) - acked_ago).isoformat(),
        latest_heartbeat_at=heartbeat,
    )


def drain(monkeypatch, *, queued=(), listed=()):
    from sprites_claude_managed_agents_dispatch import main

    async def items(work, **_kwargs):
        for item in work:
            yield item

    async def retrieve(session_id):
        return SimpleNamespace(id=session_id, metadata={})

    fake = SimpleNamespace(
        beta=SimpleNamespace(
            environments=SimpleNamespace(
                work=SimpleNamespace(
                    poller=lambda **_: items(queued), list=lambda _: items(listed)
                )
            ),
            sessions=SimpleNamespace(retrieve=retrieve),
        )
    )
    spawned = []
    monkeypatch.setattr(main, "_client", lambda: fake)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_environment_id="env_test", anthropic_environment_key="k"
        ),
    )
    monkeypatch.setattr(
        main, "spawn", lambda _session_id, *, work_id, **_: spawned.append(work_id)
    )
    asyncio.run(main._drain_work())
    return spawned


def test_drain_spawns_queued_work(monkeypatch):
    assert drain(monkeypatch, queued=[work_item("queued")]) == ["queued"]


def test_drain_retries_only_orphaned_work(monkeypatch):
    listed = [
        work_item("orphan"),
        work_item("recent", acked_ago=timedelta(seconds=1)),
        work_item("running", heartbeat=datetime.now(UTC).isoformat()),
        work_item("active", state="active"),
    ]
    assert drain(monkeypatch, listed=listed) == ["orphan"]
