import re

import pytest
from sprites_claude_managed_agents_dispatch.sandbox import sprite_name


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
