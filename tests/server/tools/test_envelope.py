"""Coverage for ok() lifting an addon-supplied `warnings` list into the envelope."""

from blender_mcp.server.tools.envelope import ok


def test_ok_lifts_data_warnings_into_envelope_and_drops_the_key() -> None:
    result = ok({"name": "Cube", "warnings": ["Undo checkpoint unavailable (global undo is disabled)"]})

    assert result["warnings"] == ["Undo checkpoint unavailable (global undo is disabled)"]
    assert result["data"] == {"name": "Cube"}  # the key is removed from data
    assert result["ok"] is True


def test_ok_merges_data_warnings_after_tool_supplied_warnings() -> None:
    result = ok(
        {"name": "Cube", "warnings": ["from addon"]},
        warnings=["from tool"],
    )

    assert result["warnings"] == ["from tool", "from addon"]
    assert "warnings" not in result["data"]


def test_ok_leaves_non_dict_data_and_missing_warnings_untouched() -> None:
    assert ok(["a", "b"])["data"] == ["a", "b"]
    assert ok(["a", "b"])["warnings"] == []
    assert ok({"name": "Cube"})["data"] == {"name": "Cube"}
    assert ok({"name": "Cube"})["warnings"] == []


def test_ok_ignores_non_list_warnings_on_data() -> None:
    # A tool payload that happens to carry a scalar `warnings` field is left as
    # data; only a list is treated as liftable notices.
    result = ok({"warnings": "not a list"})

    assert result["data"] == {"warnings": "not a list"}
    assert result["warnings"] == []
