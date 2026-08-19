import asyncio
import json

import httpx
import pytest
from sprites_claude_managed_agents_worker import files


def responder(*, status: int = 200, body: bytes = b"payload"):
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return client(handle)


def downgrading_responder():
    """Redirects the https request to a plaintext url."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://example.com/x"})
        return httpx.Response(200, content=b"payload")

    return client(handle)


def client(handle) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    )


def download_all(client: httpx.AsyncClient, spec: dict[str, str]) -> None:
    async def run() -> None:
        async with client:
            await files.download_all(client, spec)

    asyncio.run(run())


def test_download_creates_parent_directories(tmp_path):
    dest = tmp_path / "data" / "input.csv"
    download_all(responder(), {str(dest): "https://example.com/x"})
    assert dest.read_bytes() == b"payload"


def test_download_leaves_existing_files_alone(tmp_path):
    dest = tmp_path / "notes.md"
    dest.write_bytes(b"edited by the agent")
    download_all(responder(), {str(dest): "https://example.com/x"})
    assert dest.read_bytes() == b"edited by the agent"


@pytest.mark.parametrize("client", [responder(status=404), downgrading_responder()])
def test_failed_download_leaves_nothing_behind(client, tmp_path):
    dest = tmp_path / "input.csv"
    download_all(client, {str(dest): "https://example.com/x"})
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_does_not_stop_the_others(tmp_path):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(200, content=b"payload")

    download_all(
        client(handle),
        {
            str(tmp_path / "missing"): "https://example.com/missing",
            str(tmp_path / "ok"): "https://example.com/ok",
        },
    )
    assert not (tmp_path / "missing").exists()
    assert (tmp_path / "ok").read_bytes() == b"payload"


def test_requested_reads_the_dispatcher_environment(monkeypatch):
    monkeypatch.delenv(files.FILES_ENV, raising=False)
    assert files.requested() == {}
    monkeypatch.setenv(files.FILES_ENV, json.dumps({"/workspace/y": "https://x/y"}))
    assert files.requested() == {"/workspace/y": "https://x/y"}
