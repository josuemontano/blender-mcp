"""Regression coverage for the mesh_*/model_* geometry-editing addon handlers."""

import math
import sys
import types

import pytest
from conftest import load_addon_package


# region Minimal vector/quaternion/matrix math for the mathutils fake
def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _quat_mul(a, b):
    w = a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z
    x = a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y
    y = a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x
    z = a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w
    return _FakeQuaternion((w, x, y, z))


def _quat_conjugate(q):
    return _FakeQuaternion((q.w, -q.x, -q.y, -q.z))


def _quat_rotate_vec(q, v):
    qv = (q.x, q.y, q.z)
    vv = (v.x, v.y, v.z)
    t = _cross(qv, vv)
    t = (2 * t[0], 2 * t[1], 2 * t[2])
    c2 = _cross(qv, t)
    return _FakeVector(
        vv[0] + q.w * t[0] + c2[0],
        vv[1] + q.w * t[1] + c2[1],
        vv[2] + q.w * t[2] + c2[2],
    )


def _euler_to_quat(euler):
    x, y, z = euler.x, euler.y, euler.z
    cx, sx = math.cos(x / 2), math.sin(x / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    cz, sz = math.cos(z / 2), math.sin(z / 2)
    qx = _FakeQuaternion((cx, sx, 0.0, 0.0))
    qy = _FakeQuaternion((cy, 0.0, sy, 0.0))
    qz = _FakeQuaternion((cz, 0.0, 0.0, sz))
    return _quat_mul(_quat_mul(qz, qy), qx)


def _quat_to_euler(q, _order="XYZ"):
    r20 = 2 * (q.x * q.z - q.w * q.y)
    r21 = 2 * (q.y * q.z + q.w * q.x)
    r22 = 1 - 2 * (q.x * q.x + q.y * q.y)
    r10 = 2 * (q.x * q.y + q.w * q.z)
    r00 = 1 - 2 * (q.y * q.y + q.z * q.z)
    ey = math.asin(max(-1.0, min(1.0, -r20)))
    ex = math.atan2(r21, r22)
    ez = math.atan2(r10, r00)
    return _FakeVector(ex, ey, ez)


def _quat_to_axis_angle(q):
    w = max(-1.0, min(1.0, q.w))
    angle = 2 * math.acos(w)
    s = math.sqrt(max(0.0, 1 - w * w))
    if s < 1e-8:
        return _FakeVector(1.0, 0.0, 0.0), 0.0
    return _FakeVector(q.x / s, q.y / s, q.z / s), angle


def _compose(a, b):
    """Compose two TRS matrices: a's transform applied to b (a is the outer/parent transform)."""
    scaled_b_loc = _FakeVector(
        b.loc.x * a.scale.x, b.loc.y * a.scale.y, b.loc.z * a.scale.z
    )
    rotated = _quat_rotate_vec(a.rot, scaled_b_loc)
    new_loc = _FakeVector(
        rotated.x + a.loc.x, rotated.y + a.loc.y, rotated.z + a.loc.z
    )
    new_rot = _quat_mul(a.rot, b.rot)
    new_scale = _FakeVector(
        a.scale.x * b.scale.x, a.scale.y * b.scale.y, a.scale.z * b.scale.z
    )
    return _FakeMatrix(new_loc, new_rot, new_scale)
# endregion


class _FakeVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def copy(self):
        return _FakeVector(self.x, self.y, self.z)

    def to_quaternion(self):
        return _euler_to_quat(self)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __eq__(self, other):
        return tuple(self) == tuple(other)


class _FakeQuaternion:
    def __init__(self, *args):
        if not args:
            self.w, self.x, self.y, self.z = 1.0, 0.0, 0.0, 0.0
        elif len(args) == 1:
            self.w, self.x, self.y, self.z = args[0]
        elif len(args) == 2:
            axis, angle = args
            ax, ay, az = axis
            norm = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
            ax, ay, az = ax / norm, ay / norm, az / norm
            s = math.sin(angle / 2)
            self.w, self.x, self.y, self.z = math.cos(angle / 2), ax * s, ay * s, az * s
        else:
            raise TypeError("Quaternion accepts 0, 1, or 2 positional args")

    def copy(self):
        return _FakeQuaternion((self.w, self.x, self.y, self.z))

    def to_euler(self, order="XYZ"):
        return _quat_to_euler(self, order)

    def to_axis_angle(self):
        return _quat_to_axis_angle(self)

    def __eq__(self, other):
        return (self.w, self.x, self.y, self.z) == (other.w, other.x, other.y, other.z)


class _FakeMatrix:
    def __init__(self, loc, rot, scale):
        self.loc, self.rot, self.scale = loc, rot, scale

    @property
    def translation(self):
        return self.loc.copy()

    def decompose(self):
        return self.loc.copy(), self.rot.copy(), self.scale.copy()

    def __matmul__(self, point):
        scaled = _FakeVector(
            point.x * self.scale.x, point.y * self.scale.y, point.z * self.scale.z
        )
        rotated = _quat_rotate_vec(self.rot, scaled)
        return _FakeVector(
            rotated.x + self.loc.x, rotated.y + self.loc.y, rotated.z + self.loc.z
        )

    @staticmethod
    def LocRotScale(loc, rot, scale):
        return _FakeMatrix(loc, rot, scale)


class _FakeVertex:
    def __init__(self, co):
        self.co = co


class _FakeMeshData:
    def __init__(self, n_verts=8, n_edges=12, n_polys=6):
        self.vertices = [_FakeVertex(_FakeVector(float(i), 0.0, 0.0)) for i in range(n_verts)]
        self.edges = [object() for _ in range(n_edges)]
        self.polygons = [object() for _ in range(n_polys)]
        self.remesh_voxel_size = 0.1


class _FakeModifier:
    def __init__(self, name, type):
        self.name = name
        self.type = type


class _FakeModifiers(list):
    def new(self, name, type):
        mod = _FakeModifier(name, type)
        self.append(mod)
        return mod

    def get(self, name):
        return next((m for m in self if m.name == name), None)


class _FakeObject:
    def __init__(self, name, obj_type):
        self._name = name
        self._collection = None
        self.type = obj_type
        self.location = _FakeVector()
        self.rotation_euler = _FakeVector()
        self.rotation_mode = "XYZ"
        self.rotation_quaternion = _FakeQuaternion()
        self.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        self.scale = _FakeVector(1.0, 1.0, 1.0)
        self._dimensions = _FakeVector(1.0, 1.0, 1.0)
        self.parent = None
        self.data = _FakeMeshData() if obj_type == "MESH" else None
        self.modifiers = _FakeModifiers()
        self.material_slots = []
        self._custom = {}
        self.selected = False

    def _local_matrix(self):
        if self.rotation_mode == "QUATERNION":
            rot = self.rotation_quaternion.copy()
        elif self.rotation_mode == "AXIS_ANGLE":
            angle, x, y, z = self.rotation_axis_angle
            rot = _FakeQuaternion((x, y, z), angle)
        else:
            rot = _euler_to_quat(self.rotation_euler)
        return _FakeMatrix(self.location.copy(), rot, self.scale.copy())

    @property
    def matrix_world(self):
        local = self._local_matrix()
        if self.parent is None:
            return local
        return _compose(self.parent.matrix_world, local)

    @matrix_world.setter
    def matrix_world(self, mat):
        if self.parent is None:
            loc, rot, scale = mat.decompose()
        else:
            a = self.parent.matrix_world
            diff = _FakeVector(mat.loc.x - a.loc.x, mat.loc.y - a.loc.y, mat.loc.z - a.loc.z)
            unscaled = _FakeVector(diff.x / a.scale.x, diff.y / a.scale.y, diff.z / a.scale.z)
            a_conj = _quat_conjugate(a.rot)
            loc = _quat_rotate_vec(a_conj, unscaled)
            rot = _quat_mul(a_conj, mat.rot)
            scale = _FakeVector(mat.scale.x / a.scale.x, mat.scale.y / a.scale.y, mat.scale.z / a.scale.z)
        self.location = loc
        self.scale = scale
        if self.rotation_mode == "QUATERNION":
            self.rotation_quaternion = rot
        elif self.rotation_mode == "AXIS_ANGLE":
            axis, angle = _quat_to_axis_angle(rot)
            self.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        else:
            self.rotation_euler = _quat_to_euler(rot, self.rotation_mode)

    @property
    def dimensions(self):
        return self._dimensions

    @dimensions.setter
    def dimensions(self, value):
        self._dimensions = _FakeVector(*value)

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if self._collection is not None and self._name in self._collection:
            del self._collection[self._name]
            self._collection[value] = self
        self._name = value

    def evaluated_get(self, _depsgraph):
        return self

    def select_set(self, value):
        self.selected = value

    def visible_get(self):
        return True

    def __setitem__(self, key, value):
        self._custom[key] = value

    def __getitem__(self, key):
        return self._custom[key]


class _FakeObjectsCollection(dict):
    def get(self, name, default=None):
        return dict.get(self, name, default)

    def new(self, name, data):
        obj = _FakeObject(name, "MESH" if data is not None else "EMPTY")
        obj._collection = self
        self[obj.name] = obj
        return obj

    def remove(self, obj, do_unlink=True):
        self.pop(obj.name, None)


class _FakeTexturesCollection(dict):
    def get(self, name, default=None):
        return dict.get(self, name, default)

    def new(self, name, type):
        tex = types.SimpleNamespace(name=name, type=type, noise_scale=0.0)
        self[name] = tex
        return tex

    def remove(self, tex, do_unlink=True):
        self.pop(tex.name, None)


class _FakeBMElem:
    def __init__(self):
        self.select = False


class _FakeBMElemSeq(list):
    def ensure_lookup_table(self):
        pass


class _FakeBMesh:
    def __init__(self, mesh_data):
        self.verts = _FakeBMElemSeq(_FakeBMElem() for _ in mesh_data.vertices)
        self.edges = _FakeBMElemSeq(_FakeBMElem() for _ in mesh_data.edges)
        self.faces = _FakeBMElemSeq(_FakeBMElem() for _ in mesh_data.polygons)

    def select_flush(self, _value):
        pass


def _load_addon(monkeypatch):
    scene = types.SimpleNamespace(
        blendermcp_use_polyhaven=False,
        blendermcp_use_hyper3d=False,
        blendermcp_use_hunyuan3d=False,
        blendermcp_use_sketchfab=False,
    )

    objects = _FakeObjectsCollection()
    textures = _FakeTexturesCollection()
    primitive_counter = {"n": 0}

    def _make_primitive_op(prefix, obj_type="MESH"):
        def op(**kwargs):
            primitive_counter["n"] += 1
            name = f"{prefix}.{primitive_counter['n']:03d}"
            obj = _FakeObject(name, obj_type)
            obj._collection = objects
            location = kwargs.get("location", (0, 0, 0))
            rotation = kwargs.get("rotation", (0, 0, 0))
            obj.location = _FakeVector(*location)
            obj.rotation_euler = _FakeVector(*rotation)
            objects[name] = obj
            bpy.context.view_layer.objects.active = obj
            bpy.context.active_object = obj
            return {"FINISHED"}

        return op

    def _noop(**_kwargs):
        return {"FINISHED"}

    def _mode_set(mode):
        pass

    def _modifier_apply(modifier):
        obj = bpy.context.view_layer.objects.active
        mod = obj.modifiers.get(modifier)
        if mod is not None:
            obj.modifiers.remove(mod)

    def _select_all(action="SELECT"):
        pass

    bpy = types.ModuleType("bpy")
    bpy.data = types.SimpleNamespace(
        objects=objects,
        textures=textures,
    )
    bpy.context = types.SimpleNamespace(
        scene=scene,
        view_layer=types.SimpleNamespace(objects=types.SimpleNamespace(active=None)),
        active_object=None,
        collection=types.SimpleNamespace(
            objects=types.SimpleNamespace(link=lambda _obj: None)
        ),
        evaluated_depsgraph_get=lambda: object(),
    )
    bpy.types = types.SimpleNamespace(
        AddonPreferences=object,
        Operator=object,
        Panel=object,
        Scene=type("Scene", (), {}),
    )
    bpy.ops = types.SimpleNamespace(
        mesh=types.SimpleNamespace(
            primitive_cube_add=_make_primitive_op("Cube"),
            primitive_uv_sphere_add=_make_primitive_op("Sphere"),
            primitive_cylinder_add=_make_primitive_op("Cylinder"),
            primitive_cone_add=_make_primitive_op("Cone"),
            primitive_torus_add=_make_primitive_op("Torus"),
            primitive_plane_add=_make_primitive_op("Plane"),
            extrude_region_move=_noop,
            inset_faces=_noop,
            bevel=_noop,
            bridge_edge_loops=_noop,
            subdivide=_noop,
            symmetrize=_noop,
        ),
        curve=types.SimpleNamespace(
            primitive_bezier_curve_add=_make_primitive_op(
                "BezierCurve", obj_type="CURVE"
            ),
        ),
        object=types.SimpleNamespace(
            select_all=_select_all,
            mode_set=_mode_set,
            modifier_apply=_modifier_apply,
            shade_smooth=_noop,
            voxel_remesh=_noop,
        ),
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
    bmesh.from_edit_mesh = lambda mesh_data: _FakeBMesh(mesh_data)
    bmesh.update_edit_mesh = lambda _mesh_data: None

    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = lambda seq: _FakeVector(*seq)
    mathutils.Quaternion = _FakeQuaternion
    mathutils.Matrix = types.SimpleNamespace(LocRotScale=_FakeMatrix.LocRotScale)

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

    addon = load_addon_package(monkeypatch, "blender_mcp_addon_mesh_model_test")
    return addon, bpy


def _new_mesh_object(bpy, name):
    obj = bpy.data.objects.new(name, object())
    return obj


def _new_empty_object(bpy, name):
    obj = bpy.data.objects.new(name, None)
    return obj


@pytest.mark.parametrize(
    "primitive_type,expected_obj_type",
    [
        ("CUBE", "MESH"),
        ("SPHERE", "MESH"),
        ("CYLINDER", "MESH"),
        ("CONE", "MESH"),
        ("TORUS", "MESH"),
        ("PLANE", "MESH"),
        ("CURVE", "CURVE"),
        ("cube", "MESH"),
    ],
)
def test_create_primitive_dispatches_to_the_right_op(
    monkeypatch, primitive_type, expected_obj_type
):
    addon, _bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()

    result = server.create_primitive(
        primitive_type=primitive_type, name=f"obj_{primitive_type}"
    )

    assert result["name"] == f"obj_{primitive_type}"
    assert result["type"] == expected_obj_type


def test_create_primitive_rejects_unknown_type(monkeypatch):
    addon, _bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()

    with pytest.raises(ValueError, match="Unknown primitive_type"):
        server.create_primitive(primitive_type="DODECAHEDRON")


MESH_HANDLER_CALLS = [
    ("mesh_extrude", {}),
    ("mesh_inset", {}),
    ("mesh_bevel", {}),
    ("mesh_bridge", {"edge_indices": [0, 1]}),
    ("mesh_subdivide", {}),
    ("mesh_remesh", {}),
    ("mesh_solidify", {}),
    ("model_refine", {}),
    ("model_detail", {}),
    ("model_symmetrize", {}),
    ("model_mirror", {}),
    ("model_array", {}),
    ("model_radial_array", {"radius": 2.0}),
]


@pytest.mark.parametrize("handler_name,extra_kwargs", MESH_HANDLER_CALLS)
def test_mesh_handlers_reject_missing_object(monkeypatch, handler_name, extra_kwargs):
    addon, _bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()

    with pytest.raises(ValueError, match="Object not found"):
        getattr(server, handler_name)(object_name="does_not_exist", **extra_kwargs)


@pytest.mark.parametrize("handler_name,extra_kwargs", MESH_HANDLER_CALLS)
def test_mesh_handlers_reject_non_mesh_object(monkeypatch, handler_name, extra_kwargs):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_empty_object(bpy, "empty_obj")

    with pytest.raises(ValueError, match="is not a mesh"):
        getattr(server, handler_name)(object_name="empty_obj", **extra_kwargs)


def test_mesh_boolean_rejects_invalid_operation(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "target")
    _new_mesh_object(bpy, "cutter")

    with pytest.raises(ValueError, match="Invalid operation"):
        server.mesh_boolean(
            object_name="target", cutter_object_name="cutter", operation="XOR"
        )


def test_mesh_boolean_rejects_same_object(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "self_obj")

    with pytest.raises(ValueError, match="must differ"):
        server.mesh_boolean(object_name="self_obj", cutter_object_name="self_obj")

    assert bpy.data.objects.get("self_obj") is not None


def test_mesh_boolean_keeps_cutter_by_default(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "target")
    _new_mesh_object(bpy, "cutter")

    result = server.mesh_boolean(object_name="target", cutter_object_name="cutter")

    assert result["name"] == "target"
    assert bpy.data.objects.get("cutter") is not None


def test_mesh_boolean_deletes_cutter_when_requested(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "target2")
    _new_mesh_object(bpy, "cutter2")

    server.mesh_boolean(
        object_name="target2", cutter_object_name="cutter2", keep_cutter=False
    )

    assert bpy.data.objects.get("cutter2") is None


def test_model_match_reference_copies_only_flagged_components(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    obj = _new_mesh_object(bpy, "A")
    obj.location = _FakeVector(1, 2, 3)
    obj.rotation_euler = _FakeVector(0, 0, 0)
    obj.scale = _FakeVector(1, 1, 1)
    ref = _new_mesh_object(bpy, "B")
    ref.location = _FakeVector(4, 5, 6)
    ref.rotation_euler = _FakeVector(0.1, 0.2, 0.3)
    ref.scale = _FakeVector(2, 2, 2)

    result = server.model_match_reference(
        object_name="A",
        reference_object_name="B",
        match_location=True,
        match_rotation=False,
        match_scale=True,
    )

    assert result["location"] == pytest.approx([4, 5, 6])
    assert obj.rotation_euler.x == pytest.approx(0)
    assert obj.rotation_euler.y == pytest.approx(0)
    assert obj.rotation_euler.z == pytest.approx(0)
    assert result["scale"] == pytest.approx([2, 2, 2])


def test_model_match_reference_rejects_invalid_space(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "A")
    _new_mesh_object(bpy, "B")

    with pytest.raises(ValueError, match="Invalid space"):
        server.model_match_reference(
            object_name="A", reference_object_name="B", space="OBJECT"
        )


def test_model_match_reference_local_space_copies_quaternion_directly(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    obj = _new_mesh_object(bpy, "A")
    obj.rotation_mode = "QUATERNION"
    ref = _new_mesh_object(bpy, "B")
    ref.rotation_mode = "QUATERNION"
    ref.rotation_quaternion = _FakeQuaternion((0.5, 0.5, 0.5, 0.5))

    server.model_match_reference(
        object_name="A",
        reference_object_name="B",
        match_location=False,
        match_rotation=True,
        match_scale=False,
        space="LOCAL",
    )

    assert obj.rotation_quaternion == _FakeQuaternion((0.5, 0.5, 0.5, 0.5))


def test_model_match_reference_world_space_differs_from_local_across_parenting(
    monkeypatch,
):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()

    parent_a = _new_mesh_object(bpy, "ParentA")
    parent_a.location = _FakeVector(100, 0, 0)
    obj = _new_mesh_object(bpy, "A")
    obj.location = _FakeVector(0, 0, 0)
    obj.parent = parent_a

    parent_b = _new_mesh_object(bpy, "ParentB")
    parent_b.location = _FakeVector(0, 0, 0)
    ref = _new_mesh_object(bpy, "B")
    ref.location = _FakeVector(5, 5, 5)
    ref.parent = parent_b

    # ref's world position is (5, 5, 5); WORLD-space matching must land obj
    # there too, even though obj is parented 100 units away on X.
    result = server.model_match_reference(
        object_name="A",
        reference_object_name="B",
        match_rotation=False,
        match_scale=False,
        space="WORLD",
    )

    world_loc = obj.matrix_world.loc
    assert [world_loc.x, world_loc.y, world_loc.z] == pytest.approx([5, 5, 5])
    # The returned/local location differs from world location because of the
    # parent offset - this is the whole point of matching in WORLD space.
    assert result["location"] == pytest.approx([-95, 5, 5])


def test_model_blockout_dimensions_consistent_across_primitive_types(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()

    cube_result = server.model_blockout(
        name="blockout_cube", primitive_type="CUBE", size=(2, 2, 2)
    )
    sphere_result = server.model_blockout(
        name="blockout_sphere", primitive_type="SPHERE", size=(2, 2, 2)
    )

    assert cube_result["dimensions"] == pytest.approx([2, 2, 2])
    assert sphere_result["dimensions"] == pytest.approx([2, 2, 2])
    assert bpy.data.objects["blockout_cube"]["blockout"] is True


def test_model_refine_reports_evaluated_and_modifier_when_not_applied(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R")

    result = server.model_refine(object_name="R", apply=False)

    assert result["applied"] is False
    assert result["modifier"] == "Subdivision"
    assert set(result["evaluated"]) == {"vertices", "edges", "polygons"}
    assert "bounds" in result and "min" in result["bounds"] and "max" in result["bounds"]


def test_model_refine_reports_no_modifier_when_applied(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R2")

    result = server.model_refine(object_name="R2", apply=True)

    assert result["applied"] is True
    assert result["modifier"] is None


def test_model_detail_removes_texture_orphan_when_applied(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "D")

    server.model_detail(object_name="D", apply=True)

    assert bpy.data.textures.get("D_detail") is None


def test_model_detail_keeps_texture_when_not_applied(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "D2")

    server.model_detail(object_name="D2", apply=False)

    assert bpy.data.textures.get("D2_detail") is not None


def test_model_detail_subdivide_applies_extra_modifier_first(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    obj = _new_mesh_object(bpy, "D3")

    server.model_detail(object_name="D3", apply=False, subdivide=True)

    # The subdivide pass is applied (baked and removed) before Displace is added,
    # so only the live Displace modifier remains.
    names = [m.name for m in obj.modifiers]
    assert names == ["Displace"]


def test_model_radial_array_requires_a_pivot(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R")

    with pytest.raises(ValueError, match="pivot"):
        server.model_radial_array(object_name="R", count=4, axis="Z")


def test_model_radial_array_rejects_multiple_pivot_options(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R")

    with pytest.raises(ValueError, match="at most one"):
        server.model_radial_array(
            object_name="R", count=4, radius=2.0, pivot_location=(1, 0, 0)
        )


def test_model_radial_array_with_radius_offsets_pivot(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R")

    result = server.model_radial_array(object_name="R", count=4, axis="Z", radius=3.0)

    assert result["applied"] is False
    empty = bpy.data.objects.get("R_radial_pivot")
    assert empty is not None
    # axis="Z" offsets along its perpendicular axis, X (see _RADIAL_AXIS_PERP).
    assert empty.location.x == pytest.approx(-3.0)
    assert empty.rotation_euler.z == pytest.approx(2 * math.pi / 4)


def test_model_radial_array_with_explicit_pivot_location(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R")

    server.model_radial_array(
        object_name="R", count=6, axis="Z", pivot_location=(5, 5, 5)
    )

    empty = bpy.data.objects.get("R_radial_pivot")
    assert (empty.location.x, empty.location.y, empty.location.z) == (5, 5, 5)


def test_model_radial_array_with_pivot_object(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R")
    pivot = _new_mesh_object(bpy, "Pivot")
    pivot.location = _FakeVector(1, 2, 3)

    server.model_radial_array(object_name="R", count=6, pivot_object_name="Pivot")

    empty = bpy.data.objects.get("R_radial_pivot")
    assert (empty.location.x, empty.location.y, empty.location.z) == (1, 2, 3)


def test_model_radial_array_rejects_unknown_pivot_object(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R")

    with pytest.raises(ValueError, match="Pivot object not found"):
        server.model_radial_array(object_name="R", pivot_object_name="missing")


def test_model_radial_array_cleans_up_helper_empty_when_applied(monkeypatch):
    addon, bpy = _load_addon(monkeypatch)
    server = addon.BlenderMCPServer()
    _new_mesh_object(bpy, "R2")

    server.model_radial_array(object_name="R2", count=6, axis="Z", apply=True, radius=2.0)

    assert bpy.data.objects.get("R2_radial_pivot") is None
