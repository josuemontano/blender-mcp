"""Regression coverage for the agent strategy prompt's staged workflow."""

from blender_mcp.server.prompts import asset_creation_strategy


def test_prompt_checks_addon_status_before_any_other_tool() -> None:
    text = asset_creation_strategy()

    assert text.index("get_addon_status") < text.index("list_scene_objects")
    assert text.index("get_addon_status") < text.index("get_integration_status")


def test_prompt_does_not_repeat_the_contradictory_fallback_example() -> None:
    text = asset_creation_strategy()

    assert "no dedicated tool has" not in text
    assert "a primitive is explicitly requested" not in text


def test_prompt_teaches_stale_index_and_envelope_gates() -> None:
    text = asset_creation_strategy()

    assert "stale" in text.lower()
    assert '"ok"' in text
    assert '"warnings"' in text
    assert "get_mesh_data" in text
    assert "cancelled" in text.lower()


def test_prompt_scopes_world_bounding_box_to_get_object_info() -> None:
    text = asset_creation_strategy()

    assert "world_bounding_box" in text
    assert "get_object_info" in text
    assert "list_scene_objects() to see what exists" in text
