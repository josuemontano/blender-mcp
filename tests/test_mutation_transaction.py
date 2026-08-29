"""Regression coverage for the mutation_transaction rollback/checkpoint contract."""

import sys
import types

import pytest

from conftest import load_addon_package


class FakeDatablock:
    def __init__(self, name) -> None:
        self.name = name


class FakeCollection(dict):
    """Minimal stand-in for a bpy.data.* collection: dict-like plus .new()/.remove()."""

    def get(self, name, default=None):
        return dict.get(self, name, default)

    def new(self, name, *_args, **_kwargs):
        db = FakeDatablock(name)
        self[name] = db
        return db

    def remove(self, db, do_unlink=True) -> None:
        self.pop(db.name, None)

    def __iter__(self):
        return iter(list(self.values()))


# Every collection name transaction._TRACKED_COLLECTIONS iterates - a
# mutating-path test must stock all of these or _snapshot_names() raises
# AttributeError, which is exactly how the read-only tests below prove the
# wrapper was skipped: their bpy.data has none of these attributes at all.
_TRACKED_COLLECTIONS = (
    "objects",
    "meshes",
    "curves",
    "materials",
    "textures",
    "images",
    "node_groups",
    "worlds",
    "actions",
    "armatures",
    "cameras",
    "lights",
    "collections",
)


def _load_addon(monkeypatch, *, data=None):
    bpy = types.ModuleType("bpy")
    bpy.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(
            blendermcp_use_polyhaven=False,
            blendermcp_use_sketchfab=False,
            blendermcp_use_nd=False,
        ),
    )
    bpy.data = types.SimpleNamespace(**(data or {}))
    bpy.types = types.SimpleNamespace(
        AddonPreferences=object,
        Operator=object,
        Panel=object,
        Scene=type("Scene", (), {}),
    )
    bpy.ops = types.SimpleNamespace(ed=types.SimpleNamespace(undo_push=lambda **_kw: None))

    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "FloatProperty", "IntProperty", "StringProperty"):
        setattr(props, name, lambda **_kwargs: None)
    bpy.props = props

    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda fn: fn
    handlers.undo_post = []
    handlers.redo_post = []
    handlers.depsgraph_update_post = []

    app = types.ModuleType("bpy.app")
    app.version = (5, 1, 0)
    app.version_string = "5.1.0"
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

    addon = load_addon_package(monkeypatch, "blender_mcp_addon_transaction_test")
    return addon, bpy


def test_failed_mutating_handler_rolls_back_created_datablocks(monkeypatch) -> None:
    data = {name: FakeCollection() for name in _TRACKED_COLLECTIONS}
    addon, bpy = _load_addon(monkeypatch, data=data)
    server = addon.BlenderMCPServer()

    def fake_handler():
        bpy.data.materials.new(name="Orphan")
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "_build_command_handlers", lambda: {"do_mutate": fake_handler})
    undo_calls = []
    monkeypatch.setattr(bpy.ops.ed, "undo_push", lambda **kw: undo_calls.append(kw))

    response = server.execute_command_internal({"type": "do_mutate", "params": {}})

    assert response == {"status": "error", "message": "boom"}
    assert "Orphan" not in bpy.data.materials
    assert undo_calls == []


def test_successful_mutating_handler_pushes_one_undo_checkpoint(monkeypatch) -> None:
    data = {name: FakeCollection() for name in _TRACKED_COLLECTIONS}
    addon, bpy = _load_addon(monkeypatch, data=data)
    server = addon.BlenderMCPServer()

    def fake_handler():
        bpy.data.materials.new(name="Kept")
        return {"ok": True}

    monkeypatch.setattr(server, "_build_command_handlers", lambda: {"do_mutate": fake_handler})
    undo_calls = []
    monkeypatch.setattr(bpy.ops.ed, "undo_push", lambda **kw: undo_calls.append(kw))

    response = server.execute_command_internal({"type": "do_mutate", "params": {}})

    assert response == {"status": "success", "result": {"ok": True}}
    assert "Kept" in bpy.data.materials
    assert len(undo_calls) == 1
    assert "do_mutate" in undo_calls[0]["message"]


def test_read_only_command_never_snapshots_or_checkpoints(monkeypatch) -> None:
    # Deliberately no bpy.data.* collections at all - if the read-only path
    # ever touched mutation_transaction, _snapshot_names() would raise
    # AttributeError and this test would error out instead of passing.
    addon, bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()

    def fake_readonly_handler():
        return {"objects": []}

    monkeypatch.setattr(server, "_build_command_handlers", lambda: {"list_scene_objects": fake_readonly_handler})
    undo_calls = []
    monkeypatch.setattr(bpy.ops.ed, "undo_push", lambda **kw: undo_calls.append(kw))

    response = server.execute_command_internal({"type": "list_scene_objects", "params": {}})

    assert response == {"status": "success", "result": {"objects": []}}
    assert undo_calls == []


class FakeObject:
    def __init__(self, name) -> None:
        self.name = name
        self.data = types.SimpleNamespace(name=name, materials=FakeMaterialSlots())


class FakeMaterialSlots(list):
    def clear(self) -> None:
        del self[:]


def _objects_collection(*names):
    objects = FakeCollection()
    for name in names:
        objects[name] = FakeObject(name)
    return objects


def test_sync_data_name_validates_all_names_before_mutating_any(monkeypatch) -> None:
    addon, bpy = _load_addon(monkeypatch, data={"objects": _objects_collection("Existing")})
    server = addon.BlenderMCPServer()
    existing = bpy.data.objects["Existing"]
    existing.name = "Renamed"  # object name and data name now differ, as if freshly duplicated

    with pytest.raises(ValueError, match="Object not found: Missing"):
        server.sync_data_name(object_names=["Existing", "Missing"])

    assert existing.data.name == "Existing"


def test_clear_materials_validates_all_names_before_mutating_any(monkeypatch) -> None:
    addon, bpy = _load_addon(monkeypatch, data={"objects": _objects_collection("Existing")})
    server = addon.BlenderMCPServer()
    existing = bpy.data.objects["Existing"]
    existing.data.materials.append(FakeDatablock("Paint"))

    with pytest.raises(ValueError, match="Object not found: Missing"):
        server.clear_materials(object_names=["Existing", "Missing"])

    assert list(existing.data.materials) == [existing.data.materials[0]]


class FakeUtilObject:
    def __init__(self, name) -> None:
        self.name = name
        self.display_type = "SOLID"
        self.hide_render = False
        self.visible_camera = True
        self.visible_diffuse = True
        self.visible_glossy = True
        self.visible_shadow = True
        self.visible_transmission = True
        self.visible_volume_scatter = True


def test_nd_mark_as_util_validates_all_names_before_mutating_any(monkeypatch) -> None:
    objects = FakeCollection()
    existing = FakeUtilObject("Existing")
    objects["Existing"] = existing
    addon, _bpy = _load_addon(monkeypatch, data={"objects": objects})
    server = addon.BlenderMCPServer()

    with pytest.raises(ValueError, match="Object not found: Missing"):
        server.nd_mark_as_util(object_names=["Existing", "Missing"])

    assert existing.display_type == "SOLID"
    assert existing.hide_render is False
