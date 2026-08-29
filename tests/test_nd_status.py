"""Regression coverage for ND (HugeMenace) availability reporting."""

import contextlib
import sys
import types

import pytest

from conftest import load_addon_package


class _FakeModifier:
    def __init__(self, name, type_) -> None:
        self.name = name
        self.type = type_


class _FakeObject:
    def __init__(self, name, modifiers=None) -> None:
        self.name = name
        self.modifiers = list(modifiers or [])


class _FakeObjectsCollection(dict):
    def get(self, name, default=None):
        return super().get(name, default)

    def __iter__(self):
        return iter(list(self.values()))


class _FakeOverlay:
    def __init__(self) -> None:
        self.show_cavity = False
        self.show_wireframes = False
        self.show_face_orientation = False


class _FakeArea:
    def __init__(self) -> None:
        self.type = "VIEW_3D"
        self.regions = [types.SimpleNamespace(type="WINDOW")]
        self.spaces = types.SimpleNamespace(active=types.SimpleNamespace(overlay=_FakeOverlay()))


@contextlib.contextmanager
def _temp_override(**_kwargs):
    yield


def _load_addon(monkeypatch, scene, nd_installed=False, objects=None):
    bpy = types.ModuleType("bpy")
    area = _FakeArea()
    bpy.context = types.SimpleNamespace(
        scene=scene,
        screen=types.SimpleNamespace(areas=[area]),
        temp_override=_temp_override,
    )
    bpy.types = types.SimpleNamespace(
        AddonPreferences=object,
        Operator=object,
        Panel=object,
        Scene=type("Scene", (), {}),
    )
    bpy.data = types.SimpleNamespace(objects=objects if objects is not None else _FakeObjectsCollection())

    ops = types.SimpleNamespace()
    if nd_installed:
        ops.nd = types.SimpleNamespace(
            bool_vanilla=lambda *_a, **_k: {"FINISHED"},
            clean_utils=lambda *_a, **_k: {"FINISHED"},
            toggle_clear_view=lambda *_a, **_k: {"FINISHED"},
            toggle_custom_view=lambda *_a, **_k: {"FINISHED"},
            toggle_utils=lambda *_a, **_k: {"FINISHED"},
        )
    bpy.ops = ops

    props = types.ModuleType("bpy.props")
    for name in (
        "BoolProperty",
        "EnumProperty",
        "FloatProperty",
        "IntProperty",
        "StringProperty",
    ):
        setattr(props, name, lambda **_kwargs: None)
    bpy.props = props

    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda fn: fn
    handlers.undo_post = []
    handlers.redo_post = []
    handlers.depsgraph_update_post = []

    app = types.ModuleType("bpy.app")
    app.version = (4, 2, 0)
    app.version_string = "4.2.0"
    app.background = False
    app.handlers = handlers
    app.timers = types.SimpleNamespace(
        is_registered=lambda *_a, **_k: False,
        register=lambda *_a, **_k: None,
        unregister=lambda *_a, **_k: None,
    )
    bpy.app = app

    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.setitem(sys.modules, "bpy.app", app)
    monkeypatch.setitem(sys.modules, "bpy.app.handlers", handlers)
    monkeypatch.setitem(sys.modules, "mathutils", types.ModuleType("mathutils"))
    monkeypatch.setitem(sys.modules, "bmesh", types.ModuleType("bmesh"))

    requests = types.ModuleType("requests")
    requests.utils = types.SimpleNamespace(default_headers=dict)
    requests.exceptions = types.SimpleNamespace(Timeout=TimeoutError)
    monkeypatch.setitem(sys.modules, "requests", requests)

    addon = load_addon_package(monkeypatch, "blender_mcp_addon_nd_test")
    return addon


def _scene(nd_enabled):
    return types.SimpleNamespace(
        blendermcp_use_polyhaven=False,
        blendermcp_use_hyper3d=False,
        blendermcp_use_hunyuan3d=False,
        blendermcp_use_sketchfab=False,
        blendermcp_use_nd=nd_enabled,
    )


def test_disabled_nd_is_absent_from_dispatch(monkeypatch) -> None:
    addon = _load_addon(monkeypatch, _scene(nd_enabled=False), nd_installed=True)
    server = addon.BlenderMCPServer()

    status = server.get_nd_status()
    command = server._execute_command_internal({"type": "nd_boolean"})

    assert status["enabled"] is False
    assert "currently disabled" in status["message"]
    assert command == {
        "status": "error",
        "message": "Unknown command type: nd_boolean",
    }


def test_enabled_nd_without_addon_installed_is_reported_as_not_ready(monkeypatch) -> None:
    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=False)
    server = addon.BlenderMCPServer()

    status = server.get_nd_status()

    assert status["enabled"] is False
    assert "does not appear to be" in status["message"]


def test_enabled_nd_with_addon_installed_is_ready(monkeypatch) -> None:
    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=True)
    server = addon.BlenderMCPServer()

    status = server.get_nd_status()

    assert status == {
        "enabled": True,
        "message": "ND integration is enabled and the ND addon is installed and ready to use.",
    }


def test_nd_viewport_toggle_cavity_sets_overlay_property_idempotently(monkeypatch) -> None:
    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=True)
    server = addon.BlenderMCPServer()
    overlay = sys.modules["bpy"].context.screen.areas[0].spaces.active.overlay

    result = server.nd_viewport_toggle(toggle="cavity", enabled=True)

    assert result == {"toggle": "CAVITY", "enabled": True}
    assert overlay.show_cavity is True

    result_again = server.nd_viewport_toggle(toggle="CAVITY", enabled=True)

    assert result_again == {"toggle": "CAVITY", "enabled": True}
    assert overlay.show_cavity is True


def test_nd_viewport_toggle_face_orientation_can_be_turned_off(monkeypatch) -> None:
    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=True)
    server = addon.BlenderMCPServer()
    overlay = sys.modules["bpy"].context.screen.areas[0].spaces.active.overlay
    overlay.show_face_orientation = True

    result = server.nd_viewport_toggle(toggle="FACE_ORIENTATION", enabled=False)

    assert result == {"toggle": "FACE_ORIENTATION", "enabled": False}
    assert overlay.show_face_orientation is False


def test_nd_viewport_toggle_clear_view_routes_through_nd_operator_and_ignores_enabled_state(
    monkeypatch,
) -> None:
    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=True)
    server = addon.BlenderMCPServer()
    calls = []
    monkeypatch.setattr(
        sys.modules["bpy"].ops.nd,
        "toggle_clear_view",
        lambda *_a, **_k: calls.append("called") or {"FINISHED"},
    )

    result = server.nd_viewport_toggle(toggle="clear_view", enabled=True)

    assert result == {"toggle": "CLEAR_VIEW", "enabled": None}
    assert calls == ["called"]


def test_nd_viewport_toggle_rejects_unknown_toggle(monkeypatch) -> None:
    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=True)
    server = addon.BlenderMCPServer()

    with pytest.raises(ValueError, match="Invalid toggle"):
        server.nd_viewport_toggle(toggle="SILHOUETTE", enabled=True)


def test_nd_clean_utils_reports_removed_objects_and_modifiers(monkeypatch) -> None:
    objects = _FakeObjectsCollection()
    kept = _FakeObject("Kept", modifiers=[_FakeModifier("Array", "ARRAY")])
    orphan = _FakeObject("UtilCutter")
    objects["Kept"] = kept
    objects["UtilCutter"] = orphan

    def fake_clean_utils(*_a, **_k):
        del objects["UtilCutter"]
        kept.modifiers = []
        return {"FINISHED"}

    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=True, objects=objects)
    monkeypatch.setattr(sys.modules["bpy"].ops.nd, "clean_utils", fake_clean_utils)
    server = addon.BlenderMCPServer()

    result = server.nd_clean_utils()

    assert result["status"] == "cleaned"
    assert result["removed_objects"] == ["UtilCutter"]
    assert result["removed_modifiers"] == [{"object": "Kept", "modifier": "Array", "type": "ARRAY"}]


def test_nd_clean_utils_reports_nothing_removed_when_scene_is_already_clean(monkeypatch) -> None:
    objects = _FakeObjectsCollection()
    objects["Solo"] = _FakeObject("Solo", modifiers=[_FakeModifier("Bevel", "BEVEL")])

    addon = _load_addon(monkeypatch, _scene(nd_enabled=True), nd_installed=True, objects=objects)
    server = addon.BlenderMCPServer()

    result = server.nd_clean_utils()

    assert result == {
        "status": "cleaned",
        "removed_objects": [],
        "removed_modifiers": [],
    }
