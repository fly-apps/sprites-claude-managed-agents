import base64
import time

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
