import json
import re

import pytest
from sprites_claude_managed_agents_dispatch.sandbox import (
    UPLOADS_DIR,
    sprite_files,
    sprite_labels,
    sprite_name,
)


def files_metadata(spec: object) -> dict[str, str]:
    return {"files": json.dumps(spec)}


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
