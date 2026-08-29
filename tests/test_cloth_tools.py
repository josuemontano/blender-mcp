"""Server-boundary regression coverage for the typed cloth MCP surface."""

import asyncio
import sys
import types

import pytest

from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import cloth


class _StubConnection:
    def __init__(self, result=None) -> None:
        self.result = result or {"status": "ok"}
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return self.result


def _run(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def test_cloth_patch_models_forbid_unrestricted_rna_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        cloth.ClothMaterialPatch(arbitrary_rna=1)


@pytest.mark.parametrize("weight", [-0.01, 1.01])
def test_vertex_weight_assignment_rejects_out_of_range_weights(weight) -> None:
    with pytest.raises(ValidationError):
        cloth.VertexWeightAssignment(vertex_index=0, weight=weight)


def test_configure_material_serializes_only_explicit_patch_fields(monkeypatch) -> None:
    connection = _StubConnection()
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    result = _run(
        cloth.configure_cloth_material,
        object_name="Cape",
        modifier_name="Cape Cloth",
        preset="COTTON",
        patch=cloth.ClothMaterialPatch(mass=0.42),
    )

    assert result["ok"] is True
    assert result["changed_objects"] == ["Cape"]
    assert connection.calls == [
        (
            "configure_cloth_material",
            {
                "object_name": "Cape",
                "modifier_name": "Cape Cloth",
                "patch": {"mass": 0.42},
                "preset": "COTTON",
            },
        )
    ]


def test_set_weights_serializes_typed_assignments(monkeypatch) -> None:
    connection = _StubConnection()
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    _run(
        cloth.set_cloth_vertex_weights,
        object_name="Cape",
        modifier_name="Cloth",
        role="PIN_MASS",
        group_name="Shoulders",
        assignments=[cloth.VertexWeightAssignment(vertex_index=3, weight=0.75)],
        operation="REPLACE",
    )

    assert connection.calls[0] == (
        "set_cloth_vertex_weights",
        {
            "object_name": "Cape",
            "modifier_name": "Cloth",
            "role": "PIN_MASS",
            "group_name": "Shoulders",
            "assignments": [{"vertex_index": 3, "weight": 0.75}],
            "operation": "REPLACE",
        },
    )


def test_read_only_cloth_tool_does_not_report_changes(monkeypatch) -> None:
    connection = _StubConnection({"objects": [], "dependencies": []})
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    result = _run(cloth.get_cloth_simulation_info, scene_name="Scene")

    assert result["changed_objects"] == []
    assert connection.calls[0][0] == "get_cloth_simulation_info"


def test_resource_estimate_forwards_bounded_object_page(monkeypatch) -> None:
    connection = _StubConnection({"estimates": []})
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    _run(
        cloth.estimate_cloth_resources,
        scene_name="Scene",
        cloth_object_names=["Cape"],
        object_limit=10,
        object_offset=20,
    )

    assert connection.calls == [
        (
            "estimate_cloth_resources",
            {
                "scene_name": "Scene",
                "collection_name": None,
                "cloth_object_names": ["Cape"],
                "object_limit": 10,
                "object_offset": 20,
            },
        )
    ]


def test_handler_supplied_change_and_warning_metadata_wins(monkeypatch) -> None:
    connection = _StubConnection({"changed_objects": ["Cape", "BodyProxy"], "warnings": ["cache invalidated"]})
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    result = _run(
        cloth.add_cloth_collider,
        object_name="BodyProxy",
        registrations=[
            cloth.ClothColliderRegistration(
                cloth_object_name="Cape",
                cloth_modifier_name="Cloth",
                collection_name="Cloth Colliders",
            )
        ],
    )

    assert result["changed_objects"] == ["Cape", "BodyProxy"]
    assert result["warnings"] == ["cache invalidated"]


def test_all_twenty_three_public_commands_are_registered() -> None:
    names = {
        "get_cloth_simulation_info",
        "get_cloth_object_info",
        "add_cloth_simulation",
        "configure_cloth_material",
        "configure_cloth_solver",
        "set_cloth_vertex_weights",
        "configure_cloth_pinning",
        "configure_cloth_collisions",
        "add_cloth_collider",
        "configure_cloth_collider",
        "estimate_cloth_resources",
        "validate_cloth_setup",
        "configure_cloth_sewing",
        "configure_cloth_pressure",
        "configure_cloth_internal_springs",
        "configure_cloth_rest_shape",
        "configure_cloth_field_weights",
        "animate_cloth_parameters",
        "create_cloth_attachment",
        "create_character_cloth_setup",
        "sample_cloth_simulation",
        "manage_cloth_cache",
        "remove_cloth_components",
    }

    assert all(callable(getattr(cloth, name)) for name in names)
    assert set(cloth.mcp._tool_manager._tools) >= names


def _load_cloth_handler(monkeypatch):
    addon, _bpy = _load_addon(monkeypatch, data={})
    return addon, sys.modules[f"{addon.__name__}.handlers.cloth"]


class _FakeRnaProperty:
    def __init__(self, *, prop_type="FLOAT", minimum=0.0, maximum=10.0, readonly=False) -> None:
        self.type = prop_type
        self.hard_min = minimum
        self.hard_max = maximum
        self.is_readonly = readonly
        self.is_array = False
        self.array_length = 0
        self.enum_items = []


class _FakeRnaProperties(dict):
    def __iter__(self):
        return iter(self.values())


def test_handler_rna_patch_preflights_every_value_before_mutation(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    owner = types.SimpleNamespace(
        first=1.0,
        second=2.0,
        bl_rna=types.SimpleNamespace(
            properties=_FakeRnaProperties(
                first=_FakeRnaProperty(),
                second=_FakeRnaProperty(),
            )
        ),
    )

    with pytest.raises(ValueError, match="outside Blender's RNA range"):
        handler._patch_rna(owner, {"first": 5.0, "second": 99.0}, {"first", "second"})

    assert owner.first == 1.0
    assert owner.second == 2.0


def test_handler_maps_every_public_weight_role(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)

    assert set(handler._WEIGHT_ROLES) == {
        "PIN_MASS",
        "STRUCTURAL_STIFFNESS",
        "SHEAR_STIFFNESS",
        "BENDING_STIFFNESS",
        "SHRINK",
        "PRESSURE",
        "INTERNAL_SPRINGS",
        "OBJECT_COLLISION_EXCLUSION",
        "SELF_COLLISION_EXCLUSION",
    }
    assert handler._WEIGHT_ROLES["OBJECT_COLLISION_EXCLUSION"] == (
        "collision_settings",
        "vertex_group_object_collisions",
    )


def test_handler_uses_only_official_blender_51_material_presets(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)

    assert set(handler._MATERIAL_PRESETS) == {"COTTON", "SILK", "DENIM", "LEATHER", "RUBBER"}
    assert handler._MATERIAL_PRESETS["COTTON"]["mass"] == pytest.approx(0.3)
    assert handler._MATERIAL_PRESETS["LEATHER"]["bending_stiffness"] == pytest.approx(150.0)


def test_default_in_memory_caches_have_no_shared_identity(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    cache = types.SimpleNamespace(
        use_external=False,
        use_disk_cache=False,
        filepath="",
        name="",
        index=0,
    )

    assert handler._shared_cache_identity(cache) is None


def test_external_cache_identity_uses_resolved_path(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    handler.bpy.path = types.SimpleNamespace(abspath=lambda path: f"/project/{path.removeprefix('//')}")
    cache = types.SimpleNamespace(
        use_external=True,
        use_disk_cache=True,
        filepath="//cache/cloth",
        name="Cape",
        index=2,
    )

    assert handler._shared_cache_identity(cache) == ("EXTERNAL", "/project/cache/cloth", "Cape", 2)


def test_external_cache_path_requires_the_directory_itself(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    handler.bpy.path = types.SimpleNamespace(abspath=lambda _path: "/project/cache/cloth")
    monkeypatch.setattr(handler.os.path, "isdir", lambda path: path == "/project/cache/cloth")
    cache = types.SimpleNamespace(filepath="//cache/cloth")

    assert handler._external_cache_path_status(cache) == {
        "filepath": "//cache/cloth",
        "resolved": "/project/cache/cloth",
        "valid_directory": True,
    }


def test_keyed_motion_reports_largest_per_frame_channel_delta(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    location_curve = types.SimpleNamespace(
        data_path="location",
        keyframe_points=[
            types.SimpleNamespace(co=(1.0, 0.0)),
            types.SimpleNamespace(co=(3.0, 8.0)),
        ],
    )
    rotation_curve = types.SimpleNamespace(
        data_path="rotation_euler",
        keyframe_points=[
            types.SimpleNamespace(co=(1.0, 0.0)),
            types.SimpleNamespace(co=(2.0, 100.0)),
        ],
    )
    obj = types.SimpleNamespace(
        animation_data=types.SimpleNamespace(action=types.SimpleNamespace(fcurves=[rotation_curve, location_curve]))
    )

    assert handler._max_keyed_location_delta(obj) == pytest.approx(4.0)


def test_cache_range_sets_end_first_when_new_start_exceeds_old_end(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    writes = []

    class Cache:
        frame_start = 1
        frame_end = 20

        def __setattr__(self, name, value) -> None:
            writes.append((name, value))
            object.__setattr__(self, name, value)

    cache = Cache()
    handler._set_cache_frame_range(cache, 30, 60)

    assert writes == [("frame_end", 60), ("frame_start", 30)]
    assert (cache.frame_start, cache.frame_end) == (30, 60)


def test_baked_cache_refusal_names_every_blocking_modifier(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    pairs = [
        (
            types.SimpleNamespace(name="Cape"),
            types.SimpleNamespace(name="Cloth", point_cache=types.SimpleNamespace(is_baked=True)),
        ),
        (
            types.SimpleNamespace(name="Skirt"),
            types.SimpleNamespace(name="Cloth", point_cache=types.SimpleNamespace(is_baked=True)),
        ),
    ]

    with pytest.raises(ValueError, match="Cape:Cloth, Skirt:Cloth"):
        handler._reject_baked(pairs)


def test_rna_patch_rolls_back_when_assignment_fails(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)

    class Owner:
        first = 1.0
        second = 2.0
        bl_rna = types.SimpleNamespace(
            properties=_FakeRnaProperties(
                first=_FakeRnaProperty(),
                second=_FakeRnaProperty(),
            )
        )

        def __setattr__(self, name, value) -> None:
            if name == "second" and value == 4.0:
                raise RuntimeError("assignment failed")
            object.__setattr__(self, name, value)

    owner = Owner()
    with pytest.raises(RuntimeError, match="assignment failed"):
        handler._patch_rna(owner, {"first": 3.0, "second": 4.0}, {"first", "second"})

    assert owner.first == 1.0
    assert owner.second == 2.0


def test_layered_action_uses_owner_slot_channelbag(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    slot = object()
    curves = [types.SimpleNamespace(data_path="location")]
    strip = types.SimpleNamespace(type="KEYFRAME", channelbag=lambda requested: types.SimpleNamespace(fcurves=curves))
    action = types.SimpleNamespace(layers=[types.SimpleNamespace(strips=[strip])])
    animation = types.SimpleNamespace(action=action, action_slot=slot)

    assert handler._action_fcurves(animation) == curves


def test_high_resolution_collider_threshold(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    cloth_obj = types.SimpleNamespace(data=types.SimpleNamespace(polygons=[None] * 3_000))
    collider = types.SimpleNamespace(data=types.SimpleNamespace(polygons=[None] * 12_001))

    assert handler._is_high_resolution_collider(cloth_obj, collider) is True


def test_build_modifier_is_treated_as_animated_topology(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)

    assert handler._modifier_is_animated(types.SimpleNamespace(), types.SimpleNamespace(type="BUILD")) is True


def test_dispatch_advertises_cloth_and_marks_only_inspection_read_only(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()

    commands = server._build_command_handlers()

    assert "get_cloth_simulation_info" in commands
    assert "create_character_cloth_setup" in commands
    assert "manage_cloth_cache" in commands
    assert "validate_cloth_setup" in server._READ_ONLY_COMMANDS
    assert "configure_cloth_solver" not in server._READ_ONLY_COMMANDS


def test_sewing_dry_run_forwards_exact_pairs_without_reporting_changes(monkeypatch) -> None:
    connection = _StubConnection({"dry_run": True, "analysis": {"pairs": 1}})
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    result = _run(
        cloth.configure_cloth_sewing,
        object_name="Garment",
        modifier_name="Cloth",
        seam_pairs=[cloth.SewingPair(source_vertex=4, target_vertex=9)],
        sewing_force_max=25.0,
    )

    assert result["changed_objects"] == []
    assert connection.calls == [
        (
            "configure_cloth_sewing",
            {
                "object_name": "Garment",
                "modifier_name": "Cloth",
                "seam_pairs": [{"source_vertex": 4, "target_vertex": 9}],
                "sewing_force_max": 25.0,
                "create_missing_edges": False,
                "dry_run": True,
                "max_pair_distance": None,
            },
        )
    ]


def test_animation_records_and_cache_index_are_strictly_serialized(monkeypatch) -> None:
    connection = _StubConnection()
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    _run(
        cloth.animate_cloth_parameters,
        object_name="Balloon",
        cloth_modifier_name="Cloth",
        keyframes=[
            cloth.ClothAnimationKeyframe(
                owner="CLOTH_SETTINGS",
                property_name="uniform_pressure_force",
                value=3.5,
                frame=12,
                interpolation="LINEAR",
            )
        ],
    )
    _run(
        cloth.manage_cloth_cache,
        object_name="Balloon",
        modifier_name="Cloth",
        action="CONFIGURE",
        patch=cloth.PointCachePatch(index=3, frame_start=1, frame_end=80),
    )

    assert connection.calls[0][1]["keyframes"] == [
        {
            "owner": "CLOTH_SETTINGS",
            "property_name": "uniform_pressure_force",
            "value": 3.5,
            "frame": 12.0,
            "target_name": None,
            "array_index": -1,
            "interpolation": "LINEAR",
        }
    ]
    assert connection.calls[1][1]["patch"] == {"frame_start": 1, "frame_end": 80, "index": 3}


def test_character_setup_forwards_explicit_modifier_names(monkeypatch) -> None:
    connection = _StubConnection()
    monkeypatch.setattr(cloth, "get_blender_connection", lambda: connection)

    _run(
        cloth.create_character_cloth_setup,
        garment_object_name="Coat",
        armature_object_name="Rig",
        body_collider_object_names=["Torso Proxy"],
        pin_group_name="Shoulders",
        collision_collection_name="Character Colliders",
        collider_modifier_name="Body Cloth Collision",
        subdivision_modifier_name="Render Subdivision",
        solidify_modifier_name="Render Thickness",
        rest_frame=10,
        cache_frame_start=10,
        cache_frame_end=120,
    )

    params = connection.calls[0][1]
    assert params["collider_modifier_name"] == "Body Cloth Collision"
    assert params["subdivision_modifier_name"] == "Render Subdivision"
    assert params["solidify_modifier_name"] == "Render Thickness"
    assert params["rest_frame"] == 10


def test_prospective_external_cache_identity_uses_patch_values(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    handler.bpy.path = types.SimpleNamespace(abspath=lambda path: f"/project/{path.removeprefix('//')}")
    cache = types.SimpleNamespace(
        use_external=False,
        filepath="",
        name="Old",
        index=0,
    )

    identity = handler._prospective_cache_identity(
        cache,
        {"use_external": True, "filepath": "//cache/coat", "name": "Coat", "index": 4},
    )

    assert identity == ("EXTERNAL", "/project/cache/coat", "Coat", 4)


def test_dynamic_read_only_classifies_sewing_dry_run_and_cache_inspection(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()
    calls = []

    server._resolve_targets = lambda _params: (_ for _ in ()).throw(AssertionError("must remain read-only"))
    sewing = server._run_handler(
        "configure_cloth_sewing",
        lambda **params: calls.append(params) or {"dry_run": True},
        {"dry_run": True},
    )
    cache = server._run_handler(
        "manage_cloth_cache",
        lambda **params: calls.append(params) or {"point_cache": {}},
        {"action": "INSPECT"},
    )

    assert sewing == {"dry_run": True}
    assert cache == {"point_cache": {}}
    assert calls == [{"dry_run": True}, {"action": "INSPECT"}]


def test_sewing_plan_reports_duplicate_loose_edges(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)

    class Vector:
        def __init__(self, values):
            self.values = values

        def __sub__(self, other):
            return Vector(tuple(first - second for first, second in zip(self.values, other.values, strict=True)))

        @property
        def length(self):
            return sum(value * value for value in self.values) ** 0.5

    obj = types.SimpleNamespace(
        data=types.SimpleNamespace(
            vertices=[types.SimpleNamespace(co=Vector((index, 0, 0))) for index in range(6)],
            polygons=[types.SimpleNamespace(vertices=(0, 1, 2)), types.SimpleNamespace(vertices=(3, 4, 5))],
            edges=[types.SimpleNamespace(vertices=(1, 3), index=6), types.SimpleNamespace(vertices=(1, 3), index=7)],
        )
    )

    plan = handler._sewing_plan(obj, [{"source_vertex": 1, "target_vertex": 3}], None)

    assert plan["duplicate_requested_mesh_edges"] == 1
    assert plan["pairs"][0]["edge_indices"] == [6, 7]


def test_point_cache_operator_override_contains_exact_cache(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    scene = object()
    view_layer = object()
    obj = object()
    cache = object()
    monkeypatch.setattr(handler, "_scene_context_for_object", lambda _obj: (scene, view_layer))

    _scene, _view_layer, override = handler._point_cache_context(obj, cache)

    assert override["point_cache"] is cache
    assert override["object"] is obj
    assert override["scene"] is scene
    assert override["view_layer"] is view_layer


def test_owned_membership_lookup_is_exact(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)
    obj = {
        "blendermcp_cloth_component_a": (
            '{"owned": true, "role": "collision_membership", "collection": "Character Colliders"}'
        ),
        "blendermcp_cloth_component_b": ('{"owned": true, "role": "collision_membership", "collection": "Other"}'),
    }

    record = handler._owned_membership_record(obj, "Character Colliders")

    assert record["collection"] == "Character Colliders"
    assert handler._owned_membership_record(obj, "Missing") is None


def test_p1_dispatch_targets_and_geometry_capture_are_declared(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()

    assert "cloth_object_name" in server._TARGET_NAME_PARAMS
    assert "garment_object_name" in server._TARGET_NAME_PARAMS
    assert "body_collider_object_names" in server._TARGET_NAMES_PARAMS
    assert "configure_cloth_sewing" in server._GEOMETRY_MUTATING_COMMANDS


def test_field_strength_is_the_only_direct_field_animation_control(monkeypatch) -> None:
    _addon, handler = _load_cloth_handler(monkeypatch)

    assert handler._ANIMATABLE_FIELDS["FIELD_SETTINGS"] == {"strength"}
