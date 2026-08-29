from blender_mcp.server.tools.sketchfab import _preview_metadata
from blender_mcp.server.tools.viewport import _screenshot_metadata


def test_preview_metadata_reports_uid_model_name_and_author() -> None:
    result = {"model_name": "Low Poly Tree", "author": "Jane Doe", "thumbnail_width": 512, "thumbnail_height": 512}

    metadata = _preview_metadata(result, "abc123")

    assert metadata == {
        "uid": "abc123",
        "model_name": "Low Poly Tree",
        "author": "Jane Doe",
        "thumbnail_width": 512,
        "thumbnail_height": 512,
    }


def test_preview_metadata_defaults_missing_fields() -> None:
    metadata = _preview_metadata({}, "abc123")

    assert metadata == {
        "uid": "abc123",
        "model_name": "Unknown",
        "author": "Unknown",
        "thumbnail_width": None,
        "thumbnail_height": None,
    }


def test_screenshot_metadata_reports_width_height_and_method() -> None:
    result = {"width": 1000, "height": 562, "method": "offscreen"}

    metadata = _screenshot_metadata(result)

    assert metadata == {"width": 1000, "height": 562, "method": "offscreen"}


def test_screenshot_metadata_defaults_missing_fields_to_none() -> None:
    metadata = _screenshot_metadata({})

    assert metadata == {"width": None, "height": None, "method": None}
