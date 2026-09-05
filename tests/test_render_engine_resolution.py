"""Regression coverage for dynamically registered Blender render engines."""

import sys
import types

import pytest

from test_mutation_transaction import _load_addon  # ruff: ignore[import-private-name]


def test_render_engine_resolution_uses_runtime_enum_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve dynamic Cycles and Eevee identifiers through a live-instance callback."""
    addon, bpy = _load_addon(monkeypatch, data={})
    render_settings = object()
    bpy.context.scene.render = render_settings
    names = {"CYCLES": "Cycles", "BLENDER_EEVEE": "EEVEE"}
    calls: list[tuple[object, str, str]] = []

    def enum_item_name(owner: object, property_name: str, identifier: str) -> str:
        calls.append((owner, property_name, identifier))
        return names.get(identifier, "")

    bpy.types.UILayout = types.SimpleNamespace(enum_item_name=enum_item_name)
    texture_shared = sys.modules[f"{addon.__name__}.handlers.texture._shared"]
    lighting_shared = sys.modules[f"{addon.__name__}.handlers.lighting._shared"]

    assert texture_shared.runtime_engine("CYCLES") == "CYCLES"
    assert texture_shared.runtime_engine("EEVEE") == "BLENDER_EEVEE"
    assert lighting_shared.engine_identifiers() == names
    assert lighting_shared.resolve_engine("CYCLES") == "CYCLES"
    assert lighting_shared.resolve_engine("EEVEE") == "BLENDER_EEVEE"
    assert calls
    assert all(owner is render_settings and property_name == "engine" for owner, property_name, _ in calls)


def test_runtime_engine_rejects_unavailable_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject an engine when Blender's dynamic enum callback cannot resolve it."""
    addon, bpy = _load_addon(monkeypatch, data={})
    bpy.context.scene.render = object()
    bpy.types.UILayout = types.SimpleNamespace(enum_item_name=lambda *_args: "")
    texture_shared = sys.modules[f"{addon.__name__}.handlers.texture._shared"]

    with pytest.raises(ValueError, match="Render engine 'CYCLES' is unavailable"):
        texture_shared.runtime_engine("CYCLES")


def test_preview_color_management_uses_runtime_enum_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select AgX and its preferred look from Blender's dynamic OCIO enums."""
    addon, bpy = _load_addon(monkeypatch, data={})
    settings = types.SimpleNamespace(view_transform="Standard", look="None", exposure=1.25)
    scene = types.SimpleNamespace(view_settings=settings)
    names = {
        ("view_transform", "AgX"): "AgX",
        ("look", "AgX - Medium High Contrast"): "Medium High Contrast",
    }

    def enum_item_name(owner: object, property_name: str, identifier: str) -> str:
        assert owner is settings
        return names.get((property_name, identifier), "")

    bpy.types.UILayout = types.SimpleNamespace(enum_item_name=enum_item_name)
    previews = sys.modules[f"{addon.__name__}.handlers.texture.previews"]

    result = previews._configure_preview_color_management(scene)

    assert result == {"view_transform": "AgX", "look": "AgX - Medium High Contrast", "exposure": 1.25}
