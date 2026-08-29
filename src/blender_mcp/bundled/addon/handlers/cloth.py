# Blender RNA objects are dynamically generated and the surrounding add-on
# handler mixins intentionally avoid importing bpy-only annotation classes.
"""Blender-main-thread handlers for typed cloth simulation workflows."""

from __future__ import annotations

import contextlib
import json
import math
import os
import statistics
import uuid

from collections import Counter
from itertools import pairwise

import bpy

from ..helpers import paginate, preserve_mode_and_selection, set_active, sync_from_editmode

_MAX_WEIGHT_ASSIGNMENTS = 10_000
_MCP_SCHEMA_VERSION = 1
_OWNERSHIP_PREFIX = "blendermcp_cloth"
_DEFORMING_MODIFIERS = {"ARMATURE", "HOOK", "LATTICE", "MESH_DEFORM", "SURFACE_DEFORM"}
_TOPOLOGY_MODIFIERS = {
    "ARRAY",
    "BEVEL",
    "BOOLEAN",
    "BUILD",
    "DECIMATE",
    "EDGE_SPLIT",
    "MASK",
    "MIRROR",
    "NODES",
    "REMESH",
    "SCREW",
    "SKIN",
    "SOLIDIFY",
    "SUBSURF",
    "TRIANGULATE",
    "WELD",
}

_MATERIAL_FIELDS = {
    "mass",
    "air_damping",
    "bending_model",
    "tension_stiffness",
    "tension_stiffness_max",
    "compression_stiffness",
    "compression_stiffness_max",
    "shear_stiffness",
    "shear_stiffness_max",
    "bending_stiffness",
    "bending_stiffness_max",
    "tension_damping",
    "compression_damping",
    "shear_damping",
    "bending_damping",
}
_SOLVER_FIELDS = {"quality", "time_scale", "gravity", "voxel_cell_size"}
_PINNING_FIELDS = {
    "pin_stiffness",
    "goal_min",
    "goal_max",
    "goal_default",
    "goal_spring",
    "goal_friction",
}
_CLOTH_COLLISION_FIELDS = {
    "use_collision",
    "collision_quality",
    "distance_min",
    "impulse_clamp",
    "damping",
    "friction",
    "vertex_group_object_collisions",
    "use_self_collision",
    "self_distance_min",
    "self_friction",
    "self_impulse_clamp",
    "vertex_group_self_collisions",
}
_COLLIDER_FIELDS = {"use", "thickness_outer", "cloth_friction", "damping", "use_culling", "use_normal"}
_WEIGHT_ROLES = {
    "PIN_MASS": ("settings", "vertex_group_mass"),
    "STRUCTURAL_STIFFNESS": ("settings", "vertex_group_structural_stiffness"),
    "SHEAR_STIFFNESS": ("settings", "vertex_group_shear_stiffness"),
    "BENDING_STIFFNESS": ("settings", "vertex_group_bending"),
    "SHRINK": ("settings", "vertex_group_shrink"),
    "PRESSURE": ("settings", "vertex_group_pressure"),
    "INTERNAL_SPRINGS": ("settings", "vertex_group_intern"),
    "OBJECT_COLLISION_EXCLUSION": ("collision_settings", "vertex_group_object_collisions"),
    "SELF_COLLISION_EXCLUSION": ("collision_settings", "vertex_group_self_collisions"),
}

# Blender 5.1's shipped scripts/presets/cloth values. Solver quality and
# internal/pressure controls intentionally stay out of this material-only tool.
_MATERIAL_PRESETS = {
    "COTTON": {
        "mass": 0.3,
        "tension_stiffness": 15.0,
        "compression_stiffness": 15.0,
        "shear_stiffness": 15.0,
        "bending_stiffness": 0.5,
        "tension_damping": 5.0,
        "compression_damping": 5.0,
        "shear_damping": 5.0,
        "air_damping": 1.0,
    },
    "SILK": {
        "mass": 0.15,
        "tension_stiffness": 5.0,
        "compression_stiffness": 5.0,
        "shear_stiffness": 5.0,
        "bending_stiffness": 0.05,
        "tension_damping": 0.0,
        "compression_damping": 0.0,
        "shear_damping": 0.0,
        "air_damping": 1.0,
    },
    "DENIM": {
        "mass": 1.0,
        "tension_stiffness": 40.0,
        "compression_stiffness": 40.0,
        "shear_stiffness": 40.0,
        "bending_stiffness": 10.0,
        "tension_damping": 25.0,
        "compression_damping": 25.0,
        "shear_damping": 25.0,
        "air_damping": 1.0,
    },
    "LEATHER": {
        "mass": 0.4,
        "tension_stiffness": 80.0,
        "compression_stiffness": 80.0,
        "shear_stiffness": 80.0,
        "bending_stiffness": 150.0,
        "tension_damping": 25.0,
        "compression_damping": 25.0,
        "shear_damping": 25.0,
        "air_damping": 1.0,
    },
    "RUBBER": {
        "mass": 3.0,
        "tension_stiffness": 15.0,
        "compression_stiffness": 15.0,
        "shear_stiffness": 15.0,
        "bending_stiffness": 25.0,
        "tension_damping": 25.0,
        "compression_damping": 25.0,
        "shear_damping": 25.0,
        "air_damping": 1.0,
    },
}


def _finite(value, label):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if isinstance(value, (list, tuple)) and not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError(f"{label} must contain only finite numbers")
    return value


def _rna_property(owner, name):
    prop = owner.bl_rna.properties.get(name)
    if prop is None or prop.is_readonly:
        raise ValueError(f"Blender {bpy.app.version_string} does not expose writable {type(owner).__name__}.{name}")
    return prop


def _validate_rna_value(owner, name, value):
    prop = _rna_property(owner, name)
    _finite(value, name)
    if prop.type in {"FLOAT", "INT"} and not prop.is_array and (value < prop.hard_min or value > prop.hard_max):
        raise ValueError(f"{name}={value} is outside Blender's RNA range [{prop.hard_min}, {prop.hard_max}]")
    if prop.type == "ENUM" and value not in {item.identifier for item in prop.enum_items}:
        raise ValueError(f"Invalid {name}: {value}")
    if prop.is_array and len(value) != prop.array_length:
        raise ValueError(f"{name} must contain {prop.array_length} values")
    return value


def _patch_rna(owner, patch, allowed):
    patch = patch or {}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"Unsupported properties: {sorted(unknown)}")
    validated = {name: _validate_rna_value(owner, name, value) for name, value in patch.items()}
    old = {name: _serialize(getattr(owner, name)) for name in validated}
    try:
        for name, value in validated.items():
            setattr(owner, name, value)
    except Exception:
        for name, value in old.items():
            with contextlib.suppress(Exception):
                setattr(owner, name, value)
        raise
    return {name: {"old": old[name], "new": _serialize(getattr(owner, name))} for name in validated}


def _restore_rna(owner, changes):
    for name, values in changes.items():
        if name != "collection":
            with contextlib.suppress(Exception):
                setattr(owner, name, values["old"])


def _serialize(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "name"):
        return value.name
    try:
        return [_serialize(item) for item in value]
    except TypeError:
        return str(value)


def _read_fields(owner, fields):
    return {name: _serialize(getattr(owner, name)) for name in sorted(fields) if hasattr(owner, name)}


def _get_object(name, types=None):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    if types and obj.type not in types:
        raise ValueError(f"Object '{name}' must be one of {sorted(types)} (type={obj.type})")
    return obj


def _get_modifier(obj, name, modifier_type):
    modifier = obj.modifiers.get(name)
    if modifier is None:
        raise ValueError(f"Modifier not found: {name} on '{obj.name}'")
    if modifier.type != modifier_type:
        raise ValueError(f"Modifier '{name}' on '{obj.name}' is {modifier.type}, not {modifier_type}")
    return modifier


def _get_cloth(object_name, modifier_name):
    obj = _get_object(object_name, {"MESH"})
    return obj, _get_modifier(obj, modifier_name, "CLOTH")


def _cache_info(cache):
    fields = (
        "name",
        "index",
        "filepath",
        "frame_start",
        "frame_end",
        "frame_step",
        "use_disk_cache",
        "use_external",
        "use_library_path",
        "is_baked",
        "is_baking",
        "is_outdated",
        "is_frame_skip",
        "info",
    )
    return {name: _serialize(getattr(cache, name)) for name in fields if hasattr(cache, name)}


def _shared_cache_identity(cache):
    """Return only an explicit cache identity that can collide across modifiers."""
    if not cache.use_external or not cache.filepath:
        return None
    return (
        "EXTERNAL",
        os.path.normcase(os.path.normpath(bpy.path.abspath(cache.filepath))),
        str(cache.name),
        int(cache.index),
    )


def _external_cache_path_status(cache):
    resolved = bpy.path.abspath(cache.filepath) if cache.filepath else ""
    return {
        "filepath": cache.filepath,
        "resolved": resolved,
        "valid_directory": bool(resolved and os.path.isdir(resolved)),
    }


def _set_cache_frame_range(cache, frame_start, frame_end):
    """Set an already-validated cache range without transiently inverting it."""
    if frame_start > cache.frame_end:
        cache.frame_end = frame_end
        cache.frame_start = frame_start
    else:
        cache.frame_start = frame_start
        cache.frame_end = frame_end
    if cache.frame_start != frame_start or cache.frame_end != frame_end:
        raise ValueError(
            "Blender did not retain the requested cache frame range "
            f"[{frame_start}, {frame_end}] (got [{cache.frame_start}, {cache.frame_end}])"
        )


def _reject_baked(modifiers):
    baked = [f"{obj.name}:{mod.name}" for obj, mod in modifiers if mod.point_cache.is_baked]
    if baked:
        raise ValueError("Cannot change a baked cloth cache. Free the exact bake separately first: " + ", ".join(baked))


def _tag_update(obj):
    with contextlib.suppress(Exception):
        obj.update_tag(refresh={"DATA"})
    bpy.context.view_layer.update()


def _modifier_info(obj, modifier):
    return {
        "name": modifier.name,
        "type": modifier.type,
        "index": list(obj.modifiers).index(modifier),
        "show_viewport": modifier.show_viewport,
        "show_render": modifier.show_render,
    }


def _native_transform(obj):
    if obj.rotation_mode == "QUATERNION":
        rotation = list(obj.rotation_quaternion)
    elif obj.rotation_mode == "AXIS_ANGLE":
        rotation = list(obj.rotation_axis_angle)
    else:
        rotation = list(obj.rotation_euler)
    world_location, world_rotation, world_scale = obj.matrix_world.decompose()
    return {
        "local": {
            "coordinate_space": "PARENT_LOCAL",
            "location": list(obj.location),
            "rotation_mode": obj.rotation_mode,
            "rotation": rotation,
            "scale": list(obj.scale),
        },
        "world": {
            "coordinate_space": "WORLD",
            "location": list(world_location),
            "rotation_quaternion": list(world_rotation),
            "scale": list(world_scale),
            "determinant": float(obj.matrix_world.to_3x3().determinant()),
        },
    }


def _action_fcurves(animation):
    action = getattr(animation, "action", None)
    if action is None:
        return []
    slot = getattr(animation, "action_slot", None)
    layers = getattr(action, "layers", None)
    if slot is not None and layers is not None:
        curves = []
        for layer in layers:
            for strip in layer.strips:
                if getattr(strip, "type", None) != "KEYFRAME":
                    continue
                channelbag = strip.channelbag(slot)
                if channelbag is not None:
                    curves.extend(channelbag.fcurves)
        return curves
    return list(getattr(action, "fcurves", ()))


def _animation_info(obj):
    sources = []
    for label, owner in (("OBJECT", obj), ("DATA", getattr(obj, "data", None))):
        animation = getattr(owner, "animation_data", None)
        action = getattr(animation, "action", None)
        if action:
            fcurves = _action_fcurves(animation)
            sources.append(
                {
                    "owner": label,
                    "action": action.name,
                    "slot": getattr(getattr(animation, "action_slot", None), "identifier", None),
                    "fcurves": [curve.data_path for curve in fcurves[:100]],
                    "truncated": len(fcurves) > 100,
                }
            )
    shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    animation = getattr(shape_keys, "animation_data", None)
    action = getattr(animation, "action", None)
    if action:
        sources.append({"owner": "SHAPE_KEYS", "action": action.name})
    return sources


def _max_keyed_location_delta(obj):
    animation = getattr(obj, "animation_data", None)
    if getattr(animation, "action", None) is None:
        return None
    maximum = 0.0
    found = False
    for curve in _action_fcurves(animation):
        if curve.data_path != "location":
            continue
        values = sorted((float(point.co[0]), float(point.co[1])) for point in curve.keyframe_points)
        for previous, current in pairwise(values):
            frame_delta = current[0] - previous[0]
            if frame_delta > 0:
                maximum = max(maximum, abs(current[1] - previous[1]) / frame_delta)
                found = True
    return maximum if found else None


def _modifier_is_animated(obj, modifier):
    if modifier.type == "BUILD":
        return True
    with contextlib.suppress(Exception):
        modifier_path = modifier.path_from_id()
        animation = getattr(obj, "animation_data", None)
        curves = [*_action_fcurves(animation), *getattr(animation, "drivers", ())]
        return any(curve.data_path.startswith(modifier_path) for curve in curves)
    return False


def _vertex_group_stats(obj, group):
    weights = []
    assigned = 0
    for vertex in obj.data.vertices:
        entry = next((item for item in vertex.groups if item.group == group.index), None)
        if entry is not None:
            assigned += 1
            weights.append(float(entry.weight))
    return {
        "name": group.name,
        "locked": group.lock_weight,
        "assigned_vertices": assigned,
        "unassigned_vertices": len(obj.data.vertices) - assigned,
        "minimum": min(weights) if weights else None,
        "maximum": max(weights) if weights else None,
        "mean": statistics.fmean(weights) if weights else None,
        "nonzero": sum(weight > 0.0 for weight in weights),
    }


def _shape_keys(obj):
    keys = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
    if not keys:
        return []
    return [{"name": key.name, "value": float(key.value), "mute": key.mute} for key in keys]


def _evaluated_counts(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return {
            "coordinate_space": "EVALUATED_OBJECT_LOCAL",
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
        }
    finally:
        evaluated.to_mesh_clear()


def _field_relationships(settings, scene):
    weights = getattr(settings, "effector_weights", None)
    if weights is None:
        return {"weights": {}, "collection": None, "effectors": []}
    collection = getattr(weights, "collection", None)
    candidates = collection.all_objects if collection else scene.objects
    effectors = []
    for obj in candidates:
        field = getattr(obj, "field", None)
        if field and getattr(field, "type", "NONE") != "NONE":
            effectors.append({"object": obj.name, "type": field.type})
    scalar_fields = {
        prop.identifier
        for prop in weights.bl_rna.properties
        if prop.identifier not in {"rna_type", "collection"} and prop.type in {"FLOAT", "INT", "BOOLEAN"}
    }
    return {
        "weights": _read_fields(weights, scalar_fields),
        "collection": collection.name if collection else None,
        "effectors": effectors[:100],
        "truncated": len(effectors) > 100,
    }


def _cloth_info(obj, modifier, scene):
    settings = modifier.settings
    collisions = modifier.collision_settings
    solver_result = modifier.solver_result
    serialized_solver_result = None
    if solver_result is not None:
        serialized_solver_result = _read_fields(
            solver_result,
            {prop.identifier for prop in solver_result.bl_rna.properties if prop.identifier != "rna_type"},
        )
    return {
        **_modifier_info(obj, modifier),
        "settings": _read_fields(
            settings,
            _MATERIAL_FIELDS
            | _SOLVER_FIELDS
            | _PINNING_FIELDS
            | {
                "vertex_group_mass",
                "vertex_group_structural_stiffness",
                "vertex_group_shear_stiffness",
                "vertex_group_bending",
                "vertex_group_shrink",
                "vertex_group_pressure",
                "vertex_group_intern",
                "use_sewing_springs",
                "sewing_force_max",
                "use_pressure",
                "uniform_pressure_force",
                "use_pressure_volume",
                "target_volume",
                "pressure_factor",
                "fluid_density",
                "use_internal_springs",
                "internal_spring_max_length",
                "internal_spring_max_diversion",
                "internal_spring_normal_check",
                "internal_tension_stiffness",
                "internal_compression_stiffness",
                "internal_tension_stiffness_max",
                "internal_compression_stiffness_max",
                "internal_friction",
                "rest_shape_key",
                "use_dynamic_mesh",
            },
        ),
        "collision_settings": {
            **_read_fields(collisions, _CLOTH_COLLISION_FIELDS),
            "collection": collisions.collection.name if collisions.collection else None,
        },
        "field_relationships": _field_relationships(settings, scene),
        "point_cache": _cache_info(modifier.point_cache),
        "solver_status": "AVAILABLE" if solver_result is not None else "NOT_INITIALIZED",
        "solver_result": serialized_solver_result,
    }


def _collider_info(obj, modifier):
    settings = obj.collision
    all_fields = _COLLIDER_FIELDS | {
        "thickness_inner",
        "damping_factor",
        "friction_factor",
        "absorption",
        "permeability",
        "stickiness",
    }
    return {
        **_modifier_info(obj, modifier),
        "settings": _read_fields(settings, all_fields),
        "non_cloth_field_applicability": {
            "thickness_inner": "soft_body_only",
            "damping_factor": "particle_only",
            "friction_factor": "particle_only",
            "permeability": "particle_only",
            "absorption": "force_effector",
            "stickiness": "not_documented_as_cloth_specific",
        },
    }


def _scene_scope(scene_name, collection_name=None):
    scene = bpy.data.scenes.get(scene_name)
    if scene is None:
        raise ValueError(f"Scene not found: {scene_name}")
    if collection_name is None:
        return scene, list(scene.objects), None
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise ValueError(f"Collection not found: {collection_name}")
    if not _collection_in_scene(collection, scene):
        raise ValueError(f"Collection '{collection_name}' is not linked to scene '{scene_name}'")
    return scene, list(collection.all_objects), collection


def _collection_in_scene(collection, scene):
    pending = [scene.collection]
    while pending:
        current = pending.pop()
        if current == collection:
            return True
        pending.extend(current.children)
    return False


def _object_scenes(obj):
    return [scene for scene in bpy.data.scenes if obj.name in scene.objects]


def _edge_lengths(obj):
    lengths = []
    for edge in obj.data.edges:
        a, b = edge.vertices
        lengths.append(float((obj.data.vertices[a].co - obj.data.vertices[b].co).length))
    if not lengths:
        return {"count": 0, "min": None, "max": None, "mean": None, "median": None, "ratio": None}
    minimum = min(lengths)
    maximum = max(lengths)
    return {
        "count": len(lengths),
        "min": minimum,
        "max": maximum,
        "mean": statistics.fmean(lengths),
        "median": statistics.median(lengths),
        "ratio": maximum / minimum if minimum > 0 else None,
    }


def _topology_summary(obj):
    edge_face_uses = Counter()
    for polygon in obj.data.polygons:
        vertices = list(polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge_face_uses[tuple(sorted((first, second)))] += 1
    loose_edges = sum(edge_face_uses.get(tuple(sorted(edge.vertices)), 0) == 0 for edge in obj.data.edges)
    return {
        "triangles": sum(len(polygon.vertices) == 3 for polygon in obj.data.polygons),
        "quads": sum(len(polygon.vertices) == 4 for polygon in obj.data.polygons),
        "ngons": sum(len(polygon.vertices) > 4 for polygon in obj.data.polygons),
        "boundary_edges": sum(count == 1 for count in edge_face_uses.values()),
        "non_manifold_edges": sum(count != 2 for count in edge_face_uses.values()) + loose_edges,
        "loose_edges": loose_edges,
        "zero_area_faces": sum(polygon.area <= 1e-12 for polygon in obj.data.polygons),
    }


def _mesh_scale_context(obj):
    scenes = _object_scenes(obj)
    scene = scenes[0] if scenes else bpy.context.scene
    scale_length = float(scene.unit_settings.scale_length)
    local_area = sum(float(polygon.area) for polygon in obj.data.polygons)
    return {
        "scene": scene.name,
        "scene_unit_scale_length": scale_length,
        "base_surface_area_object_local_squared": local_area,
        "base_vertices_per_local_area": len(obj.data.vertices) / local_area if local_area > 0 else None,
        "edge_lengths_object_local": _edge_lengths(obj),
    }


def _affected_cloths(collider):
    affected = []
    for scene in bpy.data.scenes:
        if collider.name not in scene.objects:
            continue
        for obj in scene.objects:
            for modifier in obj.modifiers:
                if modifier.type != "CLOTH":
                    continue
                collision_settings = modifier.collision_settings
                if not collision_settings.use_collision:
                    continue
                collection = collision_settings.collection
                if collection is None or collider.name in collection.all_objects:
                    affected.append((obj, modifier))
    return affected


def _eligible_active_colliders(cloth_obj, collision_settings):
    if not collision_settings.use_collision:
        return []
    candidates = {}
    for scene in _object_scenes(cloth_obj):
        scoped = collision_settings.collection.all_objects if collision_settings.collection else scene.objects
        for candidate in scoped:
            if (
                any(modifier.type == "COLLISION" for modifier in candidate.modifiers)
                and candidate.collision
                and candidate.collision.use
            ):
                candidates[candidate.name] = candidate
    return [candidates[name] for name in sorted(candidates)]


def _tag_owned_component(obj, modifier, role):
    simulation_id = uuid.uuid4().hex
    property_name = f"{_OWNERSHIP_PREFIX}_component_{simulation_id}"
    record = {
        "owned": True,
        "simulation_id": simulation_id,
        "role": role,
        "modifier": modifier.name,
        "schema_version": _MCP_SCHEMA_VERSION,
    }
    obj[property_name] = json.dumps(record, sort_keys=True)
    return {"object_property": property_name, **record}


def _collider_order_warnings(obj, collision_modifier):
    modifier_index = list(obj.modifiers).index(collision_modifier)
    downstream = [
        modifier.name
        for modifier in list(obj.modifiers)[modifier_index + 1 :]
        if modifier.type in _DEFORMING_MODIFIERS | _TOPOLOGY_MODIFIERS
    ]
    if not downstream:
        return []
    return [
        f"Deformation/topology modifiers after Collision may not be represented by the collision surface: {downstream}"
    ]


def _is_high_resolution_collider(cloth_obj, collider):
    return len(collider.data.polygons) > max(10_000, len(cloth_obj.data.polygons) * 4)


class ClothHandlersMixin:
    """Provide production-oriented cloth inspection and mutation handlers."""

    def get_cloth_simulation_info(
        self,
        scene_name,
        collection_name=None,
        object_limit=25,
        object_offset=0,
        dependency_limit=100,
        dependency_offset=0,
    ):
        scene, scope_objects, collection = _scene_scope(scene_name, collection_name)
        candidates = [
            obj
            for obj in sorted(scope_objects, key=lambda item: item.name)
            if any(modifier.type in {"CLOTH", "COLLISION"} for modifier in obj.modifiers)
        ]
        o_start, o_end, o_truncated, o_next = paginate(len(candidates), object_offset, object_limit, 200)
        records = []
        dependencies = []
        for obj in candidates[o_start:o_end]:
            cloth = [modifier for modifier in obj.modifiers if modifier.type == "CLOTH"]
            collision = [modifier for modifier in obj.modifiers if modifier.type == "COLLISION"]
            record = {
                "object": obj.name,
                "type": obj.type,
                "collections": sorted(item.name for item in obj.users_collection),
                "transform": _native_transform(obj),
                "animation": _animation_info(obj),
                "cloth": [_cloth_info(obj, modifier, scene) for modifier in cloth],
                "colliders": [_collider_info(obj, modifier) for modifier in collision],
            }
            records.append(record)
            for modifier in cloth:
                settings = modifier.settings
                collisions = modifier.collision_settings
                names = {
                    "vertex_group": [
                        getattr(settings, name, "")
                        for name in (
                            "vertex_group_mass",
                            "vertex_group_structural_stiffness",
                            "vertex_group_shear_stiffness",
                            "vertex_group_bending",
                            "vertex_group_shrink",
                            "vertex_group_pressure",
                            "vertex_group_intern",
                        )
                    ]
                    + [
                        getattr(collisions, "vertex_group_object_collisions", ""),
                        getattr(collisions, "vertex_group_self_collisions", ""),
                    ],
                    "collision_collection": [collisions.collection.name] if collisions.collection else [],
                    "effector_collection": (
                        [settings.effector_weights.collection.name] if settings.effector_weights.collection else []
                    ),
                }
                for kind, values in names.items():
                    for value in values:
                        if value:
                            if kind == "vertex_group":
                                exists = obj.vertex_groups.get(value) is not None
                            elif kind == "collision_collection":
                                exists = _collection_in_scene(collisions.collection, scene)
                            else:
                                exists = _collection_in_scene(settings.effector_weights.collection, scene)
                            dependencies.append(
                                {
                                    "cloth_object": obj.name,
                                    "cloth_modifier": modifier.name,
                                    "kind": kind,
                                    "name": value,
                                    "missing": not exists,
                                }
                            )
                rest_shape_key = _serialize(getattr(settings, "rest_shape_key", ""))
                if rest_shape_key:
                    shape_keys = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
                    dependencies.append(
                        {
                            "cloth_object": obj.name,
                            "cloth_modifier": modifier.name,
                            "kind": "rest_shape_key",
                            "name": rest_shape_key,
                            "missing": shape_keys is None or shape_keys.get(rest_shape_key) is None,
                        }
                    )
                collision_candidates = collisions.collection.all_objects if collisions.collection else scene.objects
                for candidate in collision_candidates:
                    if any(item.type == "COLLISION" for item in candidate.modifiers):
                        collider_enabled = bool(candidate.collision and candidate.collision.use)
                        dependencies.append(
                            {
                                "cloth_object": obj.name,
                                "cloth_modifier": modifier.name,
                                "kind": "eligible_collider",
                                "name": candidate.name,
                                "excluded_by_collection": False,
                                "cloth_collision_enabled": collisions.use_collision,
                                "relationship": "ACTIVE"
                                if collisions.use_collision and collider_enabled
                                else "IN_SCOPE_BUT_DISABLED",
                            }
                        )

        d_start, d_end, d_truncated, d_next = paginate(len(dependencies), dependency_offset, dependency_limit, 1000)
        return {
            "scene": scene.name,
            "collection": collection.name if collection else None,
            "frame_observed": scene.frame_current,
            "objects": records,
            "object_page": {
                "total": len(candidates),
                "offset": o_start,
                "returned_count": len(records),
                "truncated": o_truncated,
                "next_offset": o_next,
            },
            "dependencies": dependencies[d_start:d_end],
            "dependency_page": {
                "total": len(dependencies),
                "offset": d_start,
                "returned_count": d_end - d_start,
                "truncated": d_truncated,
                "next_offset": d_next,
            },
        }

    def get_cloth_object_info(self, object_name, vertex_group_limit=50, vertex_group_offset=0):
        obj = _get_object(object_name)
        if obj.type == "MESH":
            sync_from_editmode(obj)
        relevant = [modifier for modifier in obj.modifiers if modifier.type in {"CLOTH", "COLLISION"}]
        if not relevant:
            raise ValueError(f"Object '{object_name}' has no Cloth or Collision modifier")
        scene = next((scene for scene in bpy.data.scenes if obj.name in scene.objects), bpy.context.scene)
        groups = list(obj.vertex_groups) if obj.type == "MESH" else []
        start, end, truncated, next_offset = paginate(len(groups), vertex_group_offset, vertex_group_limit, 500)
        result = {
            "object": obj.name,
            "object_type": obj.type,
            "data_type": type(obj.data).__name__ if obj.data else None,
            "collections": sorted(collection.name for collection in obj.users_collection),
            "transform": _native_transform(obj),
            "dimensions_world_aligned": list(obj.dimensions),
            "animation": _animation_info(obj),
            "modifier_stack": [_modifier_info(obj, modifier) for modifier in obj.modifiers],
            "shape_keys": _shape_keys(obj) if obj.type == "MESH" else [],
            "cloth": [_cloth_info(obj, modifier, scene) for modifier in relevant if modifier.type == "CLOTH"],
            "colliders": [_collider_info(obj, modifier) for modifier in relevant if modifier.type == "COLLISION"],
            "vertex_groups": [_vertex_group_stats(obj, group) for group in groups[start:end]],
            "vertex_group_page": {
                "total": len(groups),
                "offset": start,
                "returned_count": end - start,
                "truncated": truncated,
                "next_offset": next_offset,
            },
        }
        if obj.type == "MESH":
            result["base_geometry"] = {
                "coordinate_space": "OBJECT_LOCAL",
                "vertices": len(obj.data.vertices),
                "edges": len(obj.data.edges),
                "faces": len(obj.data.polygons),
                "edge_lengths": _edge_lengths(obj),
                "topology": _topology_summary(obj),
            }
            result["evaluated_geometry"] = _evaluated_counts(obj)
        return result

    def add_cloth_simulation(
        self,
        object_name,
        modifier_name="Cloth",
        modifier_index=None,
        existing_policy="ERROR",
        cache_frame_start=1,
        cache_frame_end=250,
        collision_collection_name=None,
        preset=None,
        material=None,
        solver=None,
        collisions=None,
    ):
        obj = _get_object(object_name, {"MESH"})
        sync_from_editmode(obj)
        if not obj.data.vertices or not obj.data.edges:
            raise ValueError(f"Mesh '{object_name}' must have nonempty vertices and edges")
        if any(not math.isfinite(value) or value == 0 for value in obj.scale):
            raise ValueError(f"Mesh '{object_name}' has an invalid zero or non-finite scale")
        if existing_policy not in {"ERROR", "REUSE"}:
            raise ValueError("existing_policy must be ERROR or REUSE")
        if cache_frame_start > cache_frame_end:
            raise ValueError("cache_frame_start must be <= cache_frame_end")
        collection = None
        if collision_collection_name:
            collection = bpy.data.collections.get(collision_collection_name)
            if collection is None:
                raise ValueError(f"Collection not found: {collision_collection_name}")
            if not any(_collection_in_scene(collection, scene) for scene in _object_scenes(obj)):
                raise ValueError(
                    f"Collection '{collision_collection_name}' is not linked to a scene containing '{object_name}'"
                )
        existing = obj.modifiers.get(modifier_name)
        created = False
        if existing:
            if existing.type != "CLOTH":
                raise ValueError(f"Modifier '{modifier_name}' already exists and is not Cloth")
            if existing_policy == "ERROR":
                raise ValueError(f"Cloth modifier '{modifier_name}' already exists on '{object_name}'")
            modifier = existing
            _reject_baked([(obj, modifier)])
        else:
            duplicate_cloth = [item.name for item in obj.modifiers if item.type == "CLOTH"]
            if duplicate_cloth:
                raise ValueError(
                    f"Object '{object_name}' already has Cloth modifiers {duplicate_cloth}; reuse one by exact name"
                )
            modifier = obj.modifiers.new(name=modifier_name, type="CLOTH")
            created = True
            bpy.context.view_layer.update()
        original_index = list(obj.modifiers).index(modifier)
        old_cache_range = (modifier.point_cache.frame_start, modifier.point_cache.frame_end)
        old_collision_collection = modifier.collision_settings.collection
        material_changes = {}
        solver_changes = {}
        collision_changes = {}
        ownership = None
        try:
            if modifier.settings is None or modifier.collision_settings is None or modifier.point_cache is None:
                raise RuntimeError("Blender did not initialize Cloth settings after dependency-graph update")
            if modifier_index is not None:
                if not 0 <= modifier_index < len(obj.modifiers):
                    raise ValueError(f"modifier_index must be in [0, {len(obj.modifiers) - 1}]")
                obj.modifiers.move(list(obj.modifiers).index(modifier), modifier_index)
            material_changes = self._configure_material(obj, modifier, material, preset)
            solver_changes = _patch_rna(modifier.settings, solver or {}, _SOLVER_FIELDS)
            collision_patch = dict(collisions or {})
            if collision_collection_name:
                collision_patch["collection_name"] = collision_collection_name
            collision_changes = self._configure_collisions(obj, modifier, collision_patch)
            cache = modifier.point_cache
            for name, value in (("frame_start", cache_frame_start), ("frame_end", cache_frame_end)):
                _validate_rna_value(cache, name, value)
            _set_cache_frame_range(cache, cache_frame_start, cache_frame_end)
            if collection is not None:
                modifier.collision_settings.collection = collection
            if created:
                ownership = _tag_owned_component(obj, modifier, "cloth")
            _tag_update(obj)
        except Exception:
            if ownership is not None:
                with contextlib.suppress(Exception):
                    del obj[ownership["object_property"]]
            if created:
                with contextlib.suppress(Exception):
                    obj.modifiers.remove(modifier)
            elif not created:
                _restore_rna(modifier.settings, material_changes)
                _restore_rna(modifier.settings, solver_changes)
                _restore_rna(modifier.collision_settings, collision_changes)
                modifier.collision_settings.collection = old_collision_collection
                modifier.point_cache.frame_start, modifier.point_cache.frame_end = old_cache_range
                with contextlib.suppress(Exception):
                    obj.modifiers.move(list(obj.modifiers).index(modifier), original_index)
            raise
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "created": created,
            "modifier_index": list(obj.modifiers).index(modifier),
            "material_changes": material_changes,
            "solver_changes": solver_changes,
            "collision_changes": collision_changes,
            "point_cache": _cache_info(modifier.point_cache),
            "collision_collection": modifier.collision_settings.collection.name
            if modifier.collision_settings.collection
            else None,
            "retained_live_dependencies": True,
            "ownership": ownership,
            "scale_and_density_context": _mesh_scale_context(obj),
            "warnings": self._scale_warnings(obj),
        }

    @staticmethod
    def _scale_warnings(obj):
        absolute = [abs(value) for value in obj.scale]
        warnings = []
        if max(absolute) / min(absolute) > 1.01:
            warnings.append(
                "Nonuniform object scale makes cloth thickness, mass, and collision distances scale-sensitive."
            )
        if obj.matrix_world.to_3x3().determinant() < 0:
            warnings.append("Negative world-transform determinant can invert cloth/collider orientation assumptions.")
        return warnings

    def _configure_material(self, obj, modifier, patch, preset):
        if _mesh_scale_context(obj)["scene_unit_scale_length"] <= 0:
            raise ValueError("Scene unit scale must be positive before configuring cloth material behavior")
        values = {}
        if preset:
            if preset not in _MATERIAL_PRESETS:
                raise ValueError(f"Unknown material preset: {preset}")
            values.update(_MATERIAL_PRESETS[preset])
        values.update(patch or {})
        if not values:
            return {}
        changes = _patch_rna(modifier.settings, values, _MATERIAL_FIELDS)
        return changes

    def configure_cloth_material(self, object_name, modifier_name, patch=None, preset=None):
        obj, modifier = _get_cloth(object_name, modifier_name)
        _reject_baked([(obj, modifier)])
        if not patch and not preset:
            raise ValueError("Provide a material patch or preset")
        scale_context = _mesh_scale_context(obj)
        if scale_context["scene_unit_scale_length"] <= 0:
            raise ValueError("Scene unit scale must be positive before configuring cloth material behavior")
        changes = self._configure_material(obj, modifier, patch, preset)
        try:
            _tag_update(obj)
        except Exception:
            _restore_rna(modifier.settings, changes)
            raise
        warnings = self._scale_warnings(obj)
        if scale_context["base_surface_area_object_local_squared"] <= 0:
            warnings.append("The base mesh has no face area; material density cannot be assessed.")
        max_group_map = {
            "tension_stiffness_max": "vertex_group_structural_stiffness",
            "compression_stiffness_max": "vertex_group_structural_stiffness",
            "shear_stiffness_max": "vertex_group_shear_stiffness",
            "bending_stiffness_max": "vertex_group_bending",
        }
        for field, group_field in max_group_map.items():
            if field in changes and not getattr(modifier.settings, group_field):
                warnings.append(f"{field} has no effect until {group_field} references a populated vertex group.")
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "preset": preset,
            "preset_version": "blender-5.1" if preset else None,
            "changes": changes,
            "point_cache": _cache_info(modifier.point_cache),
            "scale_and_density_context": scale_context,
            "warnings": warnings,
        }

    def configure_cloth_solver(self, object_name, modifier_name, patch):
        obj, modifier = _get_cloth(object_name, modifier_name)
        _reject_baked([(obj, modifier)])
        if not patch:
            raise ValueError("Solver patch cannot be empty")
        if "time_scale" in patch and patch["time_scale"] <= 0:
            raise ValueError("time_scale must be positive")
        if "voxel_cell_size" in patch and patch["voxel_cell_size"] <= 0:
            raise ValueError("voxel_cell_size must be positive")
        old_quality = modifier.settings.quality
        changes = _patch_rna(modifier.settings, patch, _SOLVER_FIELDS)
        try:
            _tag_update(obj)
        except Exception:
            _restore_rna(modifier.settings, changes)
            raise
        edge = _edge_lengths(obj)
        frame_count = modifier.point_cache.frame_end - modifier.point_cache.frame_start + 1
        ratio = modifier.settings.quality / max(old_quality, 1)
        keyed_motion = _max_keyed_location_delta(obj)
        effective_motion = keyed_motion * modifier.settings.time_scale if keyed_motion is not None else None
        warnings = self._scale_warnings(obj)
        if edge["min"] and effective_motion and effective_motion > edge["min"] * max(modifier.settings.quality, 1):
            warnings.append(
                "Keyed object motion is large relative to the smallest base edge and solver quality; "
                "test representative contact frames for tunneling."
            )
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "scene_fps": bpy.context.scene.render.fps / bpy.context.scene.render.fps_base,
            "cache_frame_count": frame_count,
            "smallest_base_edge_local": edge["min"],
            "maximum_keyed_location_channel_units_per_frame": keyed_motion,
            "time_scaled_keyed_motion_per_frame": effective_motion,
            "estimated_quality_cost_multiplier": ratio,
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": warnings,
        }

    def set_cloth_vertex_weights(self, object_name, modifier_name, role, group_name, assignments, operation="REPLACE"):
        obj, modifier = _get_cloth(object_name, modifier_name)
        sync_from_editmode(obj)
        _reject_baked([(obj, modifier)])
        if role not in _WEIGHT_ROLES:
            raise ValueError(f"Unknown cloth weight role: {role}")
        if operation not in {"REPLACE", "ADD", "SUBTRACT"}:
            raise ValueError("operation must be REPLACE, ADD, or SUBTRACT")
        if not assignments or len(assignments) > _MAX_WEIGHT_ASSIGNMENTS:
            raise ValueError(f"assignments must contain 1-{_MAX_WEIGHT_ASSIGNMENTS} entries")
        indices = [item["vertex_index"] for item in assignments]
        if len(set(indices)) != len(indices):
            raise ValueError("Each vertex_index may appear only once per request")
        total = len(obj.data.vertices)
        for item in assignments:
            index, weight = item["vertex_index"], item["weight"]
            if not 0 <= index < total:
                raise ValueError(f"Vertex index {index} out of range [0, {total - 1}]")
            _finite(weight, "weight")
            if not 0.0 <= weight <= 1.0:
                raise ValueError(f"Weight for vertex {index} must be in [0, 1]")
        group = obj.vertex_groups.get(group_name)
        created = group is None
        if group is None:
            group = obj.vertex_groups.new(name=group_name)
        if group.lock_weight:
            if created:
                obj.vertex_groups.remove(group)
            raise ValueError(f"Vertex group '{group_name}' is locked")
        owner_name, property_name = _WEIGHT_ROLES[role]
        settings_owner = getattr(modifier, owner_name)
        old_reference = getattr(settings_owner, property_name)
        old_weights = {}
        for index in indices:
            try:
                old_weights[index] = float(group.weight(index))
            except RuntimeError:
                old_weights[index] = None
        try:
            for item in assignments:
                index, requested = item["vertex_index"], float(item["weight"])
                previous = old_weights[index] or 0.0
                if operation == "ADD":
                    requested = min(1.0, previous + requested)
                elif operation == "SUBTRACT":
                    requested = max(0.0, previous - requested)
                group.add([index], requested, "REPLACE")
            setattr(settings_owner, property_name, group.name)
            _tag_update(obj)
        except Exception:
            for index, previous in old_weights.items():
                with contextlib.suppress(Exception):
                    if previous is None:
                        group.remove([index])
                    else:
                        group.add([index], previous, "REPLACE")
            setattr(settings_owner, property_name, old_reference)
            if created:
                with contextlib.suppress(Exception):
                    obj.vertex_groups.remove(group)
            raise
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "role": role,
            "mapped_property": f"{owner_name}.{property_name}",
            "group": group.name,
            "group_created": created,
            "operation": operation,
            "changed_vertices": indices,
            "statistics": _vertex_group_stats(obj, group),
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": [
                "Cloth weights changed and invalidate unbaked simulation state.",
                "If any topology-changing tool runs, query get_mesh_data again before reusing these indices.",
            ],
        }

    def configure_cloth_pinning(self, object_name, modifier_name, group_name, patch):
        obj, modifier = _get_cloth(object_name, modifier_name)
        _reject_baked([(obj, modifier)])
        group = obj.vertex_groups.get(group_name)
        if group is None:
            raise ValueError(f"Vertex group not found: {group_name}")
        if not patch:
            raise ValueError("Pinning patch cannot be empty")
        old_group = modifier.settings.vertex_group_mass
        changes = _patch_rna(modifier.settings, patch, _PINNING_FIELDS)
        try:
            modifier.settings.vertex_group_mass = group.name
            _tag_update(obj)
        except Exception:
            modifier.settings.vertex_group_mass = old_group
            for name, values in changes.items():
                setattr(modifier.settings, name, values["old"])
            raise
        stats = _vertex_group_stats(obj, group)
        warnings = []
        if stats["nonzero"] == 0:
            warnings.append("The pin group has no nonzero weights; no vertices will be pinned.")
        elif stats["maximum"] < 0.5:
            warnings.append("All pin weights are below 0.5; the attachment boundary may be weak or oscillate.")
        if stats["nonzero"] == len(obj.data.vertices):
            warnings.append("Every vertex has a nonzero pin weight; little or no cloth motion may remain.")
        cloth_index = list(obj.modifiers).index(modifier)
        upstream = [item.name for item in list(obj.modifiers)[:cloth_index] if item.type in _DEFORMING_MODIFIERS]
        downstream = [item.name for item in list(obj.modifiers)[cloth_index + 1 :] if item.type in _DEFORMING_MODIFIERS]
        if downstream:
            warnings.append(
                f"Animation/deformation modifiers after Cloth cannot drive its pinned rest position: {downstream}"
            )
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "pin_group": {"old": old_group, "new": group.name},
            "changes": changes,
            "group_statistics": stats,
            "upstream_deformers": upstream,
            "downstream_deformers": downstream,
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": warnings,
        }

    def _configure_collisions(self, obj, modifier, patch):
        patch = dict(patch or {})
        collection_name = patch.pop("collection_name", None)
        clear_collection = patch.pop("clear_collection", False)
        if collection_name and clear_collection:
            raise ValueError("collection_name and clear_collection cannot be combined")
        collection = None
        if collection_name:
            collection = bpy.data.collections.get(collection_name)
            if collection is None:
                raise ValueError(f"Collection not found: {collection_name}")
            if not any(_collection_in_scene(collection, scene) for scene in _object_scenes(obj)):
                raise ValueError(f"Collection '{collection_name}' is not linked to a scene containing '{obj.name}'")
        for field in ("vertex_group_object_collisions", "vertex_group_self_collisions"):
            if field in patch and patch[field] and obj.vertex_groups.get(patch[field]) is None:
                raise ValueError(f"Vertex group not found: {patch[field]}")
        for field in ("distance_min", "self_distance_min"):
            if field in patch and patch[field] <= 0:
                raise ValueError(f"{field} must be positive")
        old_collection = modifier.collision_settings.collection
        changes = _patch_rna(modifier.collision_settings, patch, _CLOTH_COLLISION_FIELDS)
        try:
            if collection_name or clear_collection:
                modifier.collision_settings.collection = collection if collection_name else None
                changes["collection"] = {
                    "old": old_collection.name if old_collection else None,
                    "new": collection.name if collection else None,
                }
        except Exception:
            _restore_rna(modifier.collision_settings, changes)
            modifier.collision_settings.collection = old_collection
            raise
        return changes

    def configure_cloth_collisions(self, object_name, modifier_name, patch):
        obj, modifier = _get_cloth(object_name, modifier_name)
        _reject_baked([(obj, modifier)])
        if not patch:
            raise ValueError("Collision patch cannot be empty")
        old_collection = modifier.collision_settings.collection
        changes = self._configure_collisions(obj, modifier, patch)
        try:
            _tag_update(obj)
        except Exception:
            _restore_rna(modifier.collision_settings, changes)
            modifier.collision_settings.collection = old_collection
            raise
        edges = _edge_lengths(obj)
        warnings = []
        collision = modifier.collision_settings
        if edges["min"] and collision.distance_min > edges["min"] * 0.5:
            warnings.append("Object-collision distance exceeds half the smallest base edge and may separate violently.")
        if edges["min"] and collision.self_distance_min > edges["min"] * 0.5:
            warnings.append("Self-collision distance exceeds half the smallest base edge and may separate violently.")
        colliders = _eligible_active_colliders(obj, collision)
        outer_thicknesses = [float(collider.collision.thickness_outer) for collider in colliders]
        maximum_outer_thickness = max(outer_thicknesses, default=None)
        if (
            edges["min"]
            and maximum_outer_thickness is not None
            and collision.distance_min + maximum_outer_thickness > edges["min"]
        ):
            warnings.append(
                "Combined cloth distance and maximum collider outer thickness exceed the smallest base edge; "
                "inspect initial separation and contact stability."
            )
        keyed_motion = _max_keyed_location_delta(obj)
        if edges["min"] and keyed_motion and keyed_motion > edges["min"] * max(collision.collision_quality, 1):
            warnings.append(
                "Keyed object motion is large relative to the smallest base edge and collision quality; "
                "representative-frame tests may reveal tunneling."
            )
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "scope": {
                "object_collision": collision.use_collision,
                "collection": collision.collection.name if collision.collection else None,
                "self_collision": collision.use_self_collision,
                "object_exclusion_group": collision.vertex_group_object_collisions,
                "self_exclusion_group": collision.vertex_group_self_collisions,
            },
            "distance_context": {
                "smallest_base_edge_object_local": edges["min"],
                "eligible_active_colliders": [collider.name for collider in colliders],
                "maximum_collider_outer_thickness": maximum_outer_thickness,
                "maximum_keyed_location_channel_units_per_frame": keyed_motion,
            },
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": warnings,
        }

    def add_cloth_collider(
        self,
        object_name,
        modifier_name="Collision",
        existing_policy="ERROR",
        settings=None,
        registrations=None,
    ):
        obj = _get_object(object_name, {"MESH", "CURVE"})
        if obj.type == "MESH":
            sync_from_editmode(obj)
        evaluated_geometry = _evaluated_counts(obj)
        if not evaluated_geometry["vertices"] or not evaluated_geometry["faces"]:
            raise ValueError(f"Collider '{object_name}' must evaluate to nonempty surface geometry")
        if existing_policy not in {"ERROR", "REUSE"}:
            raise ValueError("existing_policy must be ERROR or REUSE")
        registrations = registrations or []
        settings = dict(settings or {})
        if settings.get("use") is False:
            raise ValueError("add_cloth_collider requires CollisionSettings.use to remain enabled")
        settings["use"] = True
        resolved = []
        for item in registrations:
            cloth_obj, cloth_mod = _get_cloth(item["cloth_object_name"], item["cloth_modifier_name"])
            collection = bpy.data.collections.get(item["collection_name"])
            if collection is None:
                raise ValueError(f"Collection not found: {item['collection_name']}")
            if not any(_collection_in_scene(collection, scene) for scene in _object_scenes(cloth_obj)):
                raise ValueError(
                    f"Collection '{collection.name}' is not linked to a scene containing '{cloth_obj.name}'"
                )
            resolved.append((cloth_obj, cloth_mod, collection))
        _reject_baked([(cloth_obj, cloth_mod) for cloth_obj, cloth_mod, _collection in resolved])
        existing = obj.modifiers.get(modifier_name)
        created = False
        if existing:
            if existing.type != "COLLISION":
                raise ValueError(f"Modifier '{modifier_name}' already exists and is not Collision")
            if existing_policy == "ERROR":
                raise ValueError(f"Collision modifier '{modifier_name}' already exists on '{object_name}'")
            modifier = existing
        else:
            try:
                modifier = obj.modifiers.new(name=modifier_name, type="COLLISION")
                bpy.context.view_layer.update()
            except Exception as exc:
                failed = obj.modifiers.get(modifier_name)
                if failed is not None and failed.type == "COLLISION":
                    with contextlib.suppress(Exception):
                        obj.modifiers.remove(failed)
                with preserve_mode_and_selection():
                    set_active(obj)
                    result = bpy.ops.object.modifier_add(type="COLLISION")
                    if "FINISHED" not in result:
                        raise RuntimeError(
                            f"Blender Collision modifier operator did not finish: {sorted(result)}"
                        ) from exc
                    modifier = obj.modifiers[-1]
                    modifier.name = modifier_name
            created = True
        linked = []
        prior_collections = {}
        changes = {}
        ownership = None
        try:
            if obj.collision is None:
                raise RuntimeError("Blender did not initialize Object.collision")
            changes = _patch_rna(obj.collision, settings, _COLLIDER_FIELDS)
            for cloth_obj, cloth_mod, collection in resolved:
                prior_collections[cloth_obj.name, cloth_mod.name] = cloth_mod.collision_settings.collection
                if obj.name not in collection.objects:
                    collection.objects.link(obj)
                    linked.append(collection)
                cloth_mod.collision_settings.collection = collection
            if created:
                ownership = _tag_owned_component(obj, modifier, "collider")
            _tag_update(obj)
        except Exception:
            for collection in linked:
                with contextlib.suppress(Exception):
                    collection.objects.unlink(obj)
            for cloth_obj, cloth_mod, _collection in resolved:
                old = prior_collections.get((cloth_obj.name, cloth_mod.name))
                cloth_mod.collision_settings.collection = old
            if ownership is not None:
                with contextlib.suppress(Exception):
                    del obj[ownership["object_property"]]
            if created:
                with contextlib.suppress(Exception):
                    obj.modifiers.remove(modifier)
            elif obj.collision is not None:
                _restore_rna(obj.collision, changes)
            raise
        return {
            "changed_objects": [obj.name, *sorted({cloth_obj.name for cloth_obj, _mod, _col in resolved})],
            "object": obj.name,
            "modifier": modifier.name,
            "created": created,
            "evaluated_geometry": evaluated_geometry,
            "modifier_index": list(obj.modifiers).index(modifier),
            "ownership": ownership,
            "animation": _animation_info(obj),
            "settings_changes": changes,
            "registrations": [
                {
                    "cloth_object": cloth_obj.name,
                    "cloth_modifier": cloth_mod.name,
                    "collection": collection.name,
                }
                for cloth_obj, cloth_mod, collection in resolved
            ],
            "new_collection_memberships": [collection.name for collection in linked],
            "affected_cloth_caches": [_cache_info(mod.point_cache) for _cloth, mod in _affected_cloths(obj)],
            "warnings": [*self._scale_warnings(obj), *_collider_order_warnings(obj, modifier)],
        }

    def configure_cloth_collider(self, object_name, modifier_name, patch):
        obj = _get_object(object_name, {"MESH", "CURVE"})
        modifier = _get_modifier(obj, modifier_name, "COLLISION")
        affected = _affected_cloths(obj)
        _reject_baked(affected)
        if not patch:
            raise ValueError("Collider patch cannot be empty")
        for field in ("thickness_outer", "cloth_friction", "damping"):
            if field in patch and patch[field] < 0:
                raise ValueError(f"{field} must be nonnegative")
        changes = _patch_rna(obj.collision, patch, _COLLIDER_FIELDS)
        try:
            _tag_update(obj)
        except Exception:
            _restore_rna(obj.collision, changes)
            raise
        warnings = [*self._scale_warnings(obj), *_collider_order_warnings(obj, modifier)]
        if (obj.collision.use_culling or obj.collision.use_normal) and obj.type == "MESH":
            zero_area = sum(poly.area <= 1e-12 for poly in obj.data.polygons)
            if zero_area:
                warnings.append(f"One-sided collision uses normals, but {zero_area} base faces have zero area.")
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "affected_cloth_caches": [
                {"object": cloth.name, "modifier": cloth_mod.name, "point_cache": _cache_info(cloth_mod.point_cache)}
                for cloth, cloth_mod in affected
            ],
            "warnings": warnings,
        }

    def estimate_cloth_resources(
        self,
        scene_name,
        collection_name=None,
        cloth_object_names=None,
        object_limit=25,
        object_offset=0,
    ):
        scene, scope, _collection = _scene_scope(scene_name, collection_name)
        if cloth_object_names is not None:
            requested = set(cloth_object_names)
            missing = requested - {obj.name for obj in scope}
            if missing:
                raise ValueError(f"Cloth objects outside the requested scope or missing: {sorted(missing)}")
            scope = [obj for obj in scope if obj.name in requested]
        cloth_objects = sorted(
            (obj for obj in scope if any(modifier.type == "CLOTH" for modifier in obj.modifiers)),
            key=lambda obj: obj.name,
        )
        start, end, truncated, next_offset = paginate(len(cloth_objects), object_offset, object_limit, 100)
        estimates = []
        for obj in cloth_objects[start:end]:
            for modifier in obj.modifiers:
                if modifier.type != "CLOTH":
                    continue
                vertices = len(obj.data.vertices)
                edges = len(obj.data.edges)
                faces = len(obj.data.polygons)
                frames = max(1, modifier.point_cache.frame_end - modifier.point_cache.frame_start + 1)
                quality = max(1, modifier.settings.quality)
                collision_quality = max(1, modifier.collision_settings.collision_quality)
                colliders = [
                    collider
                    for collider in _eligible_active_colliders(obj, modifier.collision_settings)
                    if collider.name in scene.objects
                ]
                collider_evaluations = []
                for collider in colliders[:100]:
                    try:
                        counts = _evaluated_counts(collider)
                        collider_evaluations.append({"object": collider.name, **counts})
                    except Exception as exc:
                        collider_evaluations.append({"object": collider.name, "error": str(exc)})
                collider_faces = sum(record.get("faces", 0) for record in collider_evaluations)
                topology_modifiers = [
                    item.name
                    for item in list(obj.modifiers)[: list(obj.modifiers).index(modifier)]
                    if item.type in _TOPOLOGY_MODIFIERS
                ]
                keyed_motion = _max_keyed_location_delta(obj)
                constraint_units = quality * frames * max(1, edges + faces)
                contact_units = (
                    collision_quality * frames * vertices * max(1, collider_faces)
                    if modifier.collision_settings.use_collision
                    else 0
                )
                self_units = (
                    collision_quality * frames * vertices * vertices
                    if modifier.collision_settings.use_self_collision
                    else 0
                )
                feature_factor = 1.0
                if modifier.settings.use_internal_springs:
                    feature_factor += 1.0
                if modifier.settings.use_pressure:
                    feature_factor += 0.25
                cpu_raw = (constraint_units + contact_units * 0.01 + self_units * 0.05) * feature_factor
                memory_raw = vertices + edges * 2 + faces * 3 + (vertices * vertices if self_units else 0)
                cache_raw = vertices * frames * 3
                indices = {
                    "cpu": min(100.0, max(0.0, 12.5 * math.log10(max(1.0, cpu_raw)))),
                    "memory": min(100.0, max(0.0, 12.5 * math.log10(max(1.0, memory_raw)))),
                    "cache": min(100.0, max(0.0, 12.5 * math.log10(max(1.0, cache_raw)))),
                    "collision_pressure": min(100.0, max(0.0, 12.5 * math.log10(max(1.0, contact_units + self_units)))),
                }
                peak = max(indices.values())
                band = "LOW" if peak < 45 else "MEDIUM" if peak < 70 else "HIGH"
                estimates.append(
                    {
                        "object": obj.name,
                        "modifier": modifier.name,
                        "inputs": {
                            "vertices": vertices,
                            "edges": edges,
                            "faces": faces,
                            "frames": frames,
                            "solver_quality": quality,
                            "collision_quality": collision_quality,
                            "self_collision": modifier.collision_settings.use_self_collision,
                            "collider_count": len(colliders),
                            "collider_evaluated_faces_first_100": collider_faces,
                            "collider_evaluations": collider_evaluations,
                            "collider_evaluations_truncated": len(colliders) > 100,
                            "pressure": modifier.settings.use_pressure,
                            "internal_springs": modifier.settings.use_internal_springs,
                            "topology_modifiers_before_cloth": topology_modifiers,
                            "maximum_keyed_location_channel_units_per_frame": keyed_motion,
                            "edge_lengths_local": _edge_lengths(obj),
                        },
                        "relative_indices_0_100": indices,
                        "risk_band": band,
                        "recommendations": {
                            "preview": "Reduce quality/self-collision or use collider proxies"
                            if band == "HIGH"
                            else "Current relative settings are suitable for bounded previews",
                            "final": (
                                "Increase quality only after representative-frame validation and lock dependencies"
                            ),
                        },
                        "runtime_cache": _cache_info(modifier.point_cache),
                    }
                )
        return {
            "scene": scene.name,
            "estimates": estimates,
            "object_page": {
                "total": len(cloth_objects),
                "offset": start,
                "returned_count": end - start,
                "truncated": truncated,
                "next_offset": next_offset,
            },
            "disclaimer": "Relative deterministic indices, not byte, memory, or bake-duration promises.",
        }

    def validate_cloth_setup(
        self,
        scene_name,
        collection_name=None,
        cloth_object_names=None,
        max_findings=200,
        collision_pair_limit=64,
        evaluated_triangle_limit=250000,
    ):
        if not 1 <= max_findings <= 1000:
            raise ValueError("max_findings must be in [1, 1000]")
        if not 1 <= collision_pair_limit <= 256:
            raise ValueError("collision_pair_limit must be in [1, 256]")
        if not 1000 <= evaluated_triangle_limit <= 1_000_000:
            raise ValueError("evaluated_triangle_limit must be in [1000, 1000000]")
        scene, scope, _collection = _scene_scope(scene_name, collection_name)
        if cloth_object_names is not None:
            requested = set(cloth_object_names)
            missing = requested - {obj.name for obj in scope}
            if missing:
                raise ValueError(f"Cloth objects outside the requested scope or missing: {sorted(missing)}")
            scope = [obj for obj in scope if obj.name in requested]
        findings = []
        cache_owners = {}
        omitted_findings = 0

        def add(severity, code, obj, evidence, remediation, **extra):
            nonlocal omitted_findings
            if len(findings) < max_findings:
                findings.append(
                    {
                        "severity": severity,
                        "code": code,
                        "object": obj.name if obj else None,
                        "evidence": evidence,
                        "remediation": remediation,
                        **extra,
                    }
                )
            else:
                omitted_findings += 1

        checked = 0
        pair_count = 0
        pair_limit_reached = False
        for obj in scope:
            cloth_modifiers = [modifier for modifier in obj.modifiers if modifier.type == "CLOTH"]
            if not cloth_modifiers:
                if cloth_object_names is not None:
                    add(
                        "ERROR",
                        "MISSING_CLOTH_MODIFIER",
                        obj,
                        {"requested_as_cloth": True},
                        "Add or identify the intended Cloth modifier before validation.",
                    )
                continue
            checked += 1
            sync_from_editmode(obj)
            if len(cloth_modifiers) > 1:
                add(
                    "ERROR",
                    "DUPLICATE_CLOTH",
                    obj,
                    [m.name for m in cloth_modifiers],
                    "Keep one intentional Cloth modifier or validate each stack explicitly.",
                )
            zero_faces = [poly.index for poly in obj.data.polygons if poly.area <= 1e-12]
            if zero_faces:
                add(
                    "ERROR",
                    "ZERO_AREA_FACES",
                    obj,
                    {"count": len(zero_faces), "sample": zero_faces[:20]},
                    "Repair degenerate faces before simulation.",
                )
            diagonal = max((float(obj.dimensions.length), 1.0))
            tolerance = max(diagonal * 1e-7, 1e-9)
            buckets = Counter(
                tuple(round(float(value) / tolerance) for value in vertex.co) for vertex in obj.data.vertices
            )
            duplicate_count = sum(count - 1 for count in buckets.values() if count > 1)
            if duplicate_count:
                add(
                    "WARNING",
                    "DUPLICATE_VERTEX_POSITIONS",
                    obj,
                    {"count": duplicate_count, "tolerance": tolerance},
                    "Inspect intentional seams versus duplicate geometry; do not merge automatically.",
                )
            edge_uses = Counter()
            directed = Counter()
            for poly in obj.data.polygons:
                vertices = list(poly.vertices)
                for index, a in enumerate(vertices):
                    b = vertices[(index + 1) % len(vertices)]
                    edge_uses[tuple(sorted((a, b)))] += 1
                    directed[a, b] += 1
            boundary = sum(count == 1 for count in edge_uses.values())
            non_manifold = sum(count != 2 for count in edge_uses.values())
            inconsistent = sum(directed[b, a] == 0 for a, b in directed if edge_uses[tuple(sorted((a, b)))] == 2)
            if inconsistent:
                add(
                    "ERROR",
                    "INCONSISTENT_NORMAL_WINDING",
                    obj,
                    {"directed_edges": inconsistent},
                    "Recalculate or repair face winding after inspection.",
                )
            edges = _edge_lengths(obj)
            if edges["min"] == 0:
                add("ERROR", "ZERO_LENGTH_EDGES", obj, edges, "Repair zero-length edges before simulation.")
            elif edges["ratio"] and edges["ratio"] > 20:
                add(
                    "WARNING",
                    "EXTREME_EDGE_RATIO",
                    obj,
                    edges,
                    "Use more uniform simulation topology or a cloth proxy.",
                )
            absolute_scale = [abs(value) for value in obj.scale]
            if min(absolute_scale) == 0 or max(absolute_scale) / max(min(absolute_scale), 1e-12) > 1.01:
                add(
                    "WARNING",
                    "NONUNIFORM_SCALE",
                    obj,
                    list(obj.scale),
                    "Account for scale deliberately before tuning scale-sensitive settings.",
                )
            if obj.matrix_world.to_3x3().determinant() < 0:
                add(
                    "WARNING",
                    "NEGATIVE_DETERMINANT",
                    obj,
                    float(obj.matrix_world.to_3x3().determinant()),
                    "Inspect normals and one-sided collision behavior.",
                )
            for modifier in cloth_modifiers:
                settings = modifier.settings
                collision = modifier.collision_settings
                cache = modifier.point_cache
                cache_key = _shared_cache_identity(cache)
                if cache_key is not None:
                    cache_owners.setdefault(cache_key, []).append((obj.name, modifier.name))
                if cache.use_external:
                    path_status = _external_cache_path_status(cache)
                    if not path_status["valid_directory"]:
                        add(
                            "ERROR",
                            "INVALID_EXTERNAL_CACHE_PATH",
                            obj,
                            path_status,
                            "Choose an existing explicit cache directory before baking.",
                            modifier=modifier.name,
                        )
                if settings.use_pressure and non_manifold:
                    add(
                        "ERROR",
                        "PRESSURE_NON_MANIFOLD",
                        obj,
                        {"boundary_edges": boundary, "non_manifold_edges": non_manifold},
                        "Pressure requires a closed consistently oriented manifold surface.",
                        modifier=modifier.name,
                    )
                loose_edges = sum(edge_uses.get(tuple(sorted(edge.vertices)), 0) == 0 for edge in obj.data.edges)
                if settings.use_sewing_springs and not loose_edges:
                    add(
                        "ERROR",
                        "SEWING_WITHOUT_LOOSE_EDGES",
                        obj,
                        {"loose_edges": 0},
                        "Create and verify intentional loose sewing edges before enabling sewing springs.",
                        modifier=modifier.name,
                    )
                for owner, field in _WEIGHT_ROLES.values():
                    group_name = getattr(getattr(modifier, owner), field, "")
                    if group_name and obj.vertex_groups.get(group_name) is None:
                        add(
                            "ERROR",
                            "MISSING_VERTEX_GROUP",
                            obj,
                            {"property": f"{owner}.{field}", "group": group_name},
                            "Restore the referenced group or clear/reassign the property.",
                            modifier=modifier.name,
                        )
                pin_name = settings.vertex_group_mass
                if pin_name and obj.vertex_groups.get(pin_name):
                    stats = _vertex_group_stats(obj, obj.vertex_groups[pin_name])
                    if stats["nonzero"] == 0:
                        add(
                            "ERROR",
                            "EMPTY_PIN_GROUP",
                            obj,
                            stats,
                            "Assign deliberate nonzero pin weights.",
                            modifier=modifier.name,
                        )
                    elif stats["nonzero"] == len(obj.data.vertices):
                        add(
                            "WARNING",
                            "ALL_VERTICES_PINNED",
                            obj,
                            stats,
                            "Confirm that a fully pinned surface is intentional.",
                            modifier=modifier.name,
                        )
                elif any(
                    item.type in _DEFORMING_MODIFIERS
                    for item in list(obj.modifiers)[: list(obj.modifiers).index(modifier)]
                ):
                    add(
                        "INFO",
                        "ANIMATED_CLOTH_WITHOUT_PINS",
                        obj,
                        "Upstream deformation exists but no pin vertex group is assigned.",
                        "Confirm the entire surface should simulate freely, or assign deliberate pin weights.",
                        modifier=modifier.name,
                    )
                if collision.use_self_collision and edges["min"] and collision.self_distance_min > edges["min"] * 0.5:
                    add(
                        "WARNING",
                        "SELF_DISTANCE_TOO_LARGE",
                        obj,
                        {"distance": collision.self_distance_min, "smallest_edge": edges["min"]},
                        "Reduce self-collision distance or increase uniform mesh resolution.",
                        modifier=modifier.name,
                    )
                cloth_index = list(obj.modifiers).index(modifier)
                downstream = [m.name for m in list(obj.modifiers)[cloth_index + 1 :] if m.type in _DEFORMING_MODIFIERS]
                upstream_topology = [m.name for m in list(obj.modifiers)[:cloth_index] if m.type in _TOPOLOGY_MODIFIERS]
                animated_topology = [
                    m.name
                    for m in list(obj.modifiers)[:cloth_index]
                    if m.type in _TOPOLOGY_MODIFIERS and _modifier_is_animated(obj, m)
                ]
                if downstream:
                    add(
                        "WARNING",
                        "DEFORMER_AFTER_CLOTH",
                        obj,
                        downstream,
                        "Move animation intended to drive pins before Cloth.",
                        modifier=modifier.name,
                    )
                if upstream_topology:
                    add(
                        "WARNING",
                        "TOPOLOGY_MODIFIER_BEFORE_CLOTH",
                        obj,
                        upstream_topology,
                        "Verify topology remains constant throughout the cache range.",
                        modifier=modifier.name,
                    )
                if animated_topology:
                    add(
                        "ERROR",
                        "ANIMATED_TOPOLOGY_BEFORE_CLOTH",
                        obj,
                        animated_topology,
                        "Remove frame-varying topology from the simulation mesh or use a stable cloth proxy.",
                        modifier=modifier.name,
                    )
                effector_collection = settings.effector_weights.collection
                if effector_collection and not _collection_in_scene(effector_collection, scene):
                    add(
                        "ERROR",
                        "EFFECTOR_COLLECTION_OUTSIDE_SCENE",
                        obj,
                        effector_collection.name,
                        "Link the effector collection to this scene or choose a scene-local collection.",
                        modifier=modifier.name,
                    )
                keyed_motion = _max_keyed_location_delta(obj)
                if edges["min"] and keyed_motion and keyed_motion > edges["min"] * max(settings.quality, 1):
                    add(
                        "WARNING",
                        "FAST_KEYED_MOTION",
                        obj,
                        {
                            "maximum_location_channel_units_per_frame": keyed_motion,
                            "smallest_edge_local": edges["min"],
                            "quality": settings.quality,
                        },
                        "Use representative-frame testing and consider higher solver/collision quality.",
                        modifier=modifier.name,
                    )
                if modifier.point_cache.is_outdated:
                    add(
                        "ERROR" if modifier.point_cache.is_baked else "WARNING",
                        "BAKED_CACHE_OUTDATED" if modifier.point_cache.is_baked else "OUTDATED_CACHE",
                        obj,
                        _cache_info(modifier.point_cache),
                        "Revalidate dependencies and rebuild the exact cache when authorized.",
                        modifier=modifier.name,
                    )
                colliders = [
                    other
                    for other in scene.objects
                    if collision.use_collision
                    and any(m.type == "COLLISION" for m in other.modifiers)
                    and other.collision
                    and other.collision.use
                ]
                if collision.use_collision and collision.collection:
                    allowed = collision.collection.all_objects
                    colliders = [other for other in colliders if other.name in allowed]
                if collision.use_collision and not colliders:
                    add(
                        "WARNING",
                        "NO_ELIGIBLE_COLLIDERS",
                        obj,
                        {"collection": collision.collection.name if collision.collection else None},
                        "Add or register an intentional collider in the configured scope.",
                        modifier=modifier.name,
                    )
                for collider in colliders:
                    if pair_count >= collision_pair_limit:
                        pair_limit_reached = True
                        break
                    pair_count += 1
                    if obj.type != "MESH" or collider.type != "MESH":
                        continue
                    if _is_high_resolution_collider(obj, collider):
                        add(
                            "WARNING",
                            "HIGH_RESOLUTION_COLLIDER",
                            obj,
                            {
                                "collider": collider.name,
                                "collider_faces": len(collider.data.polygons),
                                "cloth_faces": len(obj.data.polygons),
                            },
                            "Use a dedicated lower-resolution collision proxy when possible.",
                            modifier=modifier.name,
                        )
                    if (
                        len(obj.data.polygons) > evaluated_triangle_limit
                        or len(collider.data.polygons) > evaluated_triangle_limit
                    ):
                        add(
                            "INFO",
                            "INTERSECTION_CHECK_SKIPPED",
                            obj,
                            {"collider": collider.name, "triangle_limit": evaluated_triangle_limit},
                            "Use lower-resolution collision proxies or raise the bounded limit deliberately.",
                            modifier=modifier.name,
                        )
                        continue
                    try:
                        from mathutils.bvhtree import BVHTree

                        cloth_vertices = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
                        collider_vertices = [collider.matrix_world @ vertex.co for vertex in collider.data.vertices]
                        cloth_bvh = BVHTree.FromPolygons(
                            cloth_vertices,
                            [list(poly.vertices) for poly in obj.data.polygons],
                            all_triangles=False,
                            epsilon=0.0,
                        )
                        collider_bvh = BVHTree.FromPolygons(
                            collider_vertices,
                            [list(poly.vertices) for poly in collider.data.polygons],
                            all_triangles=False,
                            epsilon=0.0,
                        )
                        overlaps = cloth_bvh.overlap(collider_bvh)
                        if overlaps:
                            add(
                                "ERROR",
                                "INITIAL_COLLIDER_INTERSECTION",
                                obj,
                                {
                                    "collider": collider.name,
                                    "overlapping_face_pairs": len(overlaps),
                                    "sample": overlaps[:20],
                                },
                                "Resolve rest-frame intersections before simulation.",
                                modifier=modifier.name,
                                frame=scene.frame_current,
                            )
                    except Exception as exc:
                        add(
                            "INFO",
                            "INTERSECTION_CHECK_INCOMPLETE",
                            obj,
                            {"collider": collider.name, "reason": str(exc)},
                            "Inspect this pair in Blender at representative frames.",
                            modifier=modifier.name,
                        )
        for cache_key, owners in cache_owners.items():
            if len(owners) > 1:
                add(
                    "ERROR",
                    "SHARED_CACHE_IDENTITY",
                    None,
                    {"cache": cache_key, "owners": owners},
                    "Give every cloth modifier a unique cache name/path/index before baking.",
                )
        if pair_limit_reached:
            add(
                "INFO",
                "COLLISION_PAIR_LIMIT_REACHED",
                None,
                {"limit": collision_pair_limit},
                "Run a narrower collection/object validation for remaining pairs.",
            )
        truncated = omitted_findings > 0
        severity_counts = Counter(item["severity"] for item in findings)
        return {
            "scene": scene.name,
            "frame_observed": scene.frame_current,
            "cloth_objects_checked": checked,
            "collision_pairs_checked": pair_count,
            "findings": findings,
            "severity_counts": dict(severity_counts),
            "truncated": truncated,
            "omitted_findings": omitted_findings,
            "claim": "Structural preflight only; representative evaluated-frame review is still required.",
        }
