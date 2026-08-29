"""Regression coverage for get_scene_info pagination and the new get_mesh_data tool."""

import sys
import types

import pytest

from conftest import load_addon_package


class _FakeVector:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x, self.y, self.z = x, y, z

    def __iter__(self):
        return iter((self.x, self.y, self.z))


class _FakeVertex:
    def __init__(self, index, co=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0), select=False) -> None:
        self.index = index
        self.co = _FakeVector(*co)
        self.normal = _FakeVector(*normal)
        self.select = select


class _FakeEdge:
    def __init__(self, index, vertices=(0, 1), select=False) -> None:
        self.index = index
        self.vertices = vertices
        self.select = select


class _FakePolygon:
    def __init__(
        self,
        index,
        vertices=(0, 1, 2, 3),
        normal=(0.0, 0.0, 1.0),
        select=False,
        material_index=0,
        loop_start=0,
        loop_total=4,
    ) -> None:
        self.index = index
        self.vertices = vertices
        self.normal = _FakeVector(*normal)
        self.select = select
        self.material_index = material_index
        self.loop_start = loop_start
        self.loop_total = loop_total

    @property
    def loop_indices(self):
        return range(self.loop_start, self.loop_start + self.loop_total)


class _FakeLoop:
    def __init__(self, index, vertex_index=0, edge_index=0, normal=(0.0, 0.0, 1.0)) -> None:
        self.index = index
        self.vertex_index = vertex_index
        self.edge_index = edge_index
        self.normal = _FakeVector(*normal)


class _FakeMeshData:
    """A minimal but structurally-real mesh: n_polys quads, 4 loops each."""

    def __init__(self, n_verts=8, n_edges=12, n_polys=6) -> None:
        self.vertices = [_FakeVertex(i, co=(float(i), 0.0, 0.0)) for i in range(n_verts)]
        self.edges = [_FakeEdge(i, vertices=(i % n_verts, (i + 1) % n_verts)) for i in range(n_edges)]
        loops_per_poly = 4
        self.polygons = [
            _FakePolygon(
                i,
                vertices=(0, 1, 2, 3),
                loop_start=i * loops_per_poly,
                loop_total=loops_per_poly,
            )
            for i in range(n_polys)
        ]
        self.loops = [
            _FakeLoop(
                i,
                vertex_index=i % n_verts,
                edge_index=i % n_edges,
            )
            for i in range(n_polys * loops_per_poly)
        ]

    def calc_normals_split(self) -> None:
        pass


class _FakeObjectsCollection(dict):
    def get(self, name, default=None):
        return dict.get(self, name, default)

    def new(self, name, data):
        obj = types.SimpleNamespace(
            name=name,
            type="MESH" if data is not None else "EMPTY",
            location=_FakeVector(),
            data=data,
        )
        self[name] = obj
        return obj


def _load_addon(monkeypatch):
    objects = _FakeObjectsCollection()
    materials = []

    scene = types.SimpleNamespace(
        name="Scene",
        objects=objects.values(),
        blendermcp_use_polyhaven=False,
        blendermcp_use_hyper3d=False,
        blendermcp_use_hunyuan3d=False,
        blendermcp_use_sketchfab=False,
    )

    bpy = types.ModuleType("bpy")
    bpy.data = types.SimpleNamespace(objects=objects, materials=materials)
    bpy.context = types.SimpleNamespace(scene=scene)
    bpy.types = types.SimpleNamespace(
        AddonPreferences=object,
        Operator=object,
        Panel=object,
        Scene=type("Scene", (), {}),
    )
    bpy.ops = types.SimpleNamespace(
        mesh=types.SimpleNamespace(),
        object=types.SimpleNamespace(),
        curve=types.SimpleNamespace(),
    )

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

    bmesh = types.ModuleType("bmesh")
    mathutils = types.ModuleType("mathutils")

    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.setitem(sys.modules, "bpy.app", app)
    monkeypatch.setitem(sys.modules, "bpy.app.handlers", handlers)
    monkeypatch.setitem(sys.modules, "mathutils", mathutils)
    monkeypatch.setitem(sys.modules, "bmesh", bmesh)

    requests = types.ModuleType("requests")
    requests.utils = types.SimpleNamespace(default_headers=dict)
    requests.exceptions = types.SimpleNamespace(Timeout=TimeoutError)
    monkeypatch.setitem(sys.modules, "requests", requests)

    addon = load_addon_package(monkeypatch, "blender_mcp_addon_inspection_test")
    return addon, bpy, objects, scene


def _new_mesh_object(bpy, name, **mesh_kwargs):
    obj = bpy.data.objects.new(name, _FakeMeshData(**mesh_kwargs))
    return obj


def _new_empty_object(bpy, name):
    return bpy.data.objects.new(name, None)


# region get_scene_info pagination
def test_get_scene_info_default_returns_everything_under_the_default_limit(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    for i in range(5):
        _new_mesh_object(bpy, f"obj{i}")

    result = server.get_scene_info()

    assert result["object_count"] == 5
    assert result["returned_count"] == 5
    assert len(result["objects"]) == 5
    assert result["truncated"] is False
    assert result["next_offset"] is None


def test_get_scene_info_paginates_and_reports_truncation(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    for i in range(7):
        _new_mesh_object(bpy, f"obj{i}")

    page1 = server.get_scene_info(limit=3, offset=0)
    assert page1["object_count"] == 7
    assert page1["returned_count"] == 3
    assert page1["truncated"] is True
    assert page1["next_offset"] == 3

    page2 = server.get_scene_info(limit=3, offset=3)
    assert page2["returned_count"] == 3
    assert page2["truncated"] is True
    assert page2["next_offset"] == 6

    page3 = server.get_scene_info(limit=3, offset=6)
    assert page3["returned_count"] == 1
    assert page3["truncated"] is False
    assert page3["next_offset"] is None

    seen = {o["name"] for p in (page1, page2, page3) for o in p["objects"]}
    assert seen == {f"obj{i}" for i in range(7)}


def test_get_scene_info_offset_past_end_returns_empty_page(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "only_obj")

    result = server.get_scene_info(limit=10, offset=50)

    assert result["returned_count"] == 0
    assert result["truncated"] is False
    assert result["next_offset"] is None


# endregion


# region get_mesh_data
def test_get_mesh_data_rejects_missing_object(monkeypatch) -> None:
    addon, _bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()

    with pytest.raises(ValueError, match="Object not found"):
        server.get_mesh_data(object_name="does_not_exist")


def test_get_mesh_data_rejects_non_mesh_object(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_empty_object(bpy, "empty_obj")

    with pytest.raises(ValueError, match="is not a mesh"):
        server.get_mesh_data(object_name="empty_obj")


def test_get_mesh_data_rejects_invalid_element_type(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj")

    with pytest.raises(ValueError, match="Invalid element_type"):
        server.get_mesh_data(object_name="obj", element_type="normals")


def test_get_mesh_data_vertices_default_page(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj", n_verts=8)

    result = server.get_mesh_data(object_name="obj", element_type="vertices")

    assert result["element_type"] == "vertices"
    assert result["total"] == 8
    assert result["total_unfiltered"] == 8
    assert result["returned_count"] == 8
    assert result["truncated"] is False
    first = result["elements"][0]
    assert first["index"] == 0
    assert first["co"] == [0.0, 0.0, 0.0]
    assert first["normal"] == [0.0, 0.0, 1.0]
    assert first["select"] is False


def test_get_mesh_data_paginates_and_reports_truncation(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj", n_verts=10)

    page1 = server.get_mesh_data(object_name="obj", element_type="vertices", limit=4, offset=0)
    assert [e["index"] for e in page1["elements"]] == [0, 1, 2, 3]
    assert page1["truncated"] is True
    assert page1["next_offset"] == 4

    page2 = server.get_mesh_data(object_name="obj", element_type="vertices", limit=4, offset=page1["next_offset"])
    assert [e["index"] for e in page2["elements"]] == [4, 5, 6, 7]
    assert page2["truncated"] is True
    assert page2["next_offset"] == 8

    page3 = server.get_mesh_data(object_name="obj", element_type="vertices", limit=4, offset=page2["next_offset"])
    assert [e["index"] for e in page3["elements"]] == [8, 9]
    assert page3["truncated"] is False
    assert page3["next_offset"] is None


def test_get_mesh_data_edges_shape(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj", n_edges=3)

    result = server.get_mesh_data(object_name="obj", element_type="edges")

    assert result["total"] == 3
    assert result["elements"][0] == {"index": 0, "vertices": [0, 1], "select": False}


def test_get_mesh_data_faces_shape(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj", n_polys=2)

    result = server.get_mesh_data(object_name="obj", element_type="faces")

    assert result["total"] == 2
    face = result["elements"][0]
    assert face["index"] == 0
    assert face["vertices"] == [0, 1, 2, 3]
    assert face["normal"] == [0.0, 0.0, 1.0]
    assert face["material_index"] == 0


def test_get_mesh_data_loops_shape_and_face_index_mapping(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj", n_polys=2)

    result = server.get_mesh_data(object_name="obj", element_type="loops")

    assert result["total"] == 8  # 2 faces * 4 loops
    loops = result["elements"]
    # First 4 loops belong to face 0, next 4 to face 1
    assert [loop["face_index"] for loop in loops[:4]] == [0, 0, 0, 0]
    assert [loop["face_index"] for loop in loops[4:8]] == [1, 1, 1, 1]
    assert set(loops[0]) == {"index", "vertex_index", "edge_index", "face_index", "normal"}


def test_get_mesh_data_loops_rejects_selected_only(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj")

    with pytest.raises(ValueError, match="selected_only"):
        server.get_mesh_data(object_name="obj", element_type="loops", selected_only=True)


def test_get_mesh_data_selected_only_filters_before_paging(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    obj = _new_mesh_object(bpy, "obj", n_verts=6)
    for i in (1, 3, 5):
        obj.data.vertices[i].select = True

    result = server.get_mesh_data(object_name="obj", element_type="vertices", selected_only=True)

    assert result["total"] == 3
    assert result["total_unfiltered"] == 6
    assert [e["index"] for e in result["elements"]] == [1, 3, 5]
    assert all(e["select"] for e in result["elements"])


def test_get_mesh_data_limit_is_clamped_to_max(monkeypatch) -> None:
    addon, bpy, _objects, _scene = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "obj", n_verts=5)

    result = server.get_mesh_data(object_name="obj", element_type="vertices", limit=999999)

    # A huge requested limit is silently clamped server-side (max 1000), so a
    # 5-vertex mesh still returns everything in one page rather than erroring.
    assert result["returned_count"] == 5
    assert result["truncated"] is False


# endregion
