import json
import re

import httpx
import pytest
from sprites_claude_managed_agents_dispatch.sandbox import (
    METADATA_MAX_BYTES,
    UPLOADS_DIR,
    resolve_metadata,
    sprite_files,
    sprite_labels,
    sprite_name,
)

METADATA_URL = "https://example.com/session.json"


def files_metadata(spec: object) -> dict[str, str]:
    return {"files": json.dumps(spec)}


def hosting(body: object, *, status: int = 200) -> httpx.Client:
    """A client serving ``body`` as the hosted metadata payload."""
    content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    return httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)


def resolve(metadata: dict[str, str], client: httpx.Client) -> dict[str, str]:
    with client:
        return resolve_metadata(metadata, client=client)


def test_sprite_name_deterministic_and_safe():
    name = sprite_name("session_01AbC/xyz")
    assert name == sprite_name("session_01AbC/xyz")
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*[a-z0-9]", name)
    assert name.startswith("claude-agent-")


def test_sprite_name_length_fits():
    assert len(sprite_name("session_" + "x" * 200)) <= 63


def test_sprite_name_rejects_ids_with_no_usable_characters():
    with pytest.raises(ValueError):
        sprite_name("!!!")


def test_sprite_labels_ignore_blanks_and_duplicates():
    assert sprite_labels({"labels": " prod ,,prod,priority, "}) == ["prod", "priority"]


def test_sprite_labels_without_metadata():
    assert sprite_labels({}) == []
    assert sprite_labels({"labels": ""}) == []


def test_sprite_files_roots_mount_paths_under_the_uploads_dir():
    files = sprite_files(
        files_metadata(
            {
                "/data.csv": "https://example.com/x",
                "/nested/notes.md": "https://example.com/y",
            }
        )
    )
    assert files == {
        f"{UPLOADS_DIR}/data.csv": "https://example.com/x",
        f"{UPLOADS_DIR}/nested/notes.md": "https://example.com/y",
    }


@pytest.mark.parametrize("path", ["", "/", "/dir/", "files/", "/dir/."])
def test_sprite_files_rejects_mount_paths_without_a_file_name(path):
    with pytest.raises(ValueError):
        sprite_files(files_metadata({path: "https://example.com/a/data.csv?v=1"}))


def test_sprite_files_without_metadata():
    assert sprite_files({}) == {}
    assert sprite_files({"files": "  "}) == {}


@pytest.mark.parametrize("path", ["/../escape", "/data/../../escape", "/.."])
def test_sprite_files_rejects_mount_paths_outside_the_uploads_dir(path):
    with pytest.raises(ValueError):
        sprite_files(files_metadata({path: "https://example.com/x"}))


@pytest.mark.parametrize(
    "url", ["http://example.com/x", "file:///etc/passwd", "example.com/x"]
)
def test_sprite_files_rejects_urls_that_are_not_https(url):
    with pytest.raises(ValueError):
        sprite_files(files_metadata({"/x": url}))


def test_sprite_files_rejects_malformed_specs():
    with pytest.raises(ValueError):
        sprite_files({"files": "not json"})
    with pytest.raises(ValueError):
        sprite_files(files_metadata(["https://example.com/x"]))
    with pytest.raises(ValueError):
        sprite_files(files_metadata({"/x": 1}))


def test_resolve_metadata_without_a_url():
    assert resolve_metadata({"labels": "prod"}) == {"labels": "prod"}
    with pytest.raises(ValueError):
        assert resolve_metadata({"metadata": "  "}) == {}


def test_resolve_metadata_encodes_nested_values_as_json():
    resolved = resolve(
        {"metadata": METADATA_URL},
        hosting({"files": {"/data.csv": "https://example.com/x"}}),
    )
    assert sprite_files(resolved) == {
        f"{UPLOADS_DIR}/data.csv": "https://example.com/x"
    }


def test_resolve_metadata_rejects_urls_that_are_not_https():
    with pytest.raises(ValueError):
        resolve_metadata({"metadata": "http://example.com/session.json"})


def test_resolve_metadata_rejects_a_redirect_off_https():
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://example.com/x"})
        return httpx.Response(200, content=b"{}")

    client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)
    with pytest.raises(ValueError):
        resolve({"metadata": METADATA_URL}, client)


def test_resolve_metadata_rejects_an_oversized_payload():
    with pytest.raises(ValueError):
        resolve(
            {"metadata": METADATA_URL},
            hosting({"notes": "x" * (METADATA_MAX_BYTES + 1)}),
        )


def test_resolve_metadata_rejects_malformed_payloads():
    with pytest.raises(ValueError):
        resolve({"metadata": METADATA_URL}, hosting(b"not json"))
    with pytest.raises(ValueError):
        resolve({"metadata": METADATA_URL}, hosting(["labels"]))
    with pytest.raises(ValueError):
        resolve({"metadata": METADATA_URL}, hosting({"metadata": METADATA_URL}))


def test_resolve_metadata_raises_on_an_error_response():
    with pytest.raises(httpx.HTTPStatusError):
        resolve({"metadata": METADATA_URL}, hosting({}, status=404))
