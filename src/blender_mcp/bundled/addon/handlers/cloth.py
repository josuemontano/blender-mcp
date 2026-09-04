# Blender RNA objects are dynamically generated and the surrounding add-on
# handler mixins intentionally avoid importing bpy-only annotation classes.
"""Blender-main-thread handlers for typed cloth simulation workflows."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
import uuid

from collections import Counter
from itertools import pairwise

import bmesh
import bpy
import mathutils

from ..helpers import paginate, preserve_mode_and_selection, set_active, sync_from_editmode
from .simulation_cache import point_cache_identity, point_cache_info, set_cache_frame_range

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
_PRESSURE_FIELDS = {
    "use_pressure",
    "uniform_pressure_force",
    "use_pressure_volume",
    "target_volume",
    "pressure_factor",
    "fluid_density",
    "vertex_group_pressure",
}
_INTERNAL_SPRING_FIELDS = {
    "use_internal_springs",
    "internal_spring_max_length",
    "internal_spring_max_diversion",
    "internal_spring_normal_check",
    "internal_tension_stiffness",
    "internal_compression_stiffness",
    "internal_tension_stiffness_max",
    "internal_compression_stiffness_max",
    "internal_friction",
    "vertex_group_intern",
}
_FIELD_WEIGHT_FIELDS = {
    "all",
    "gravity",
    "force",
    "vortex",
    "magnetic",
    "wind",
    "curve_guide",
    "texture",
    "harmonic",
    "charge",
    "lennardjones",
    "turbulence",
    "drag",
    "boid",
    "smokeflow",
    "apply_to_hair_growing",
}
_POINT_CACHE_FIELDS = {
    "frame_start",
    "frame_end",
    "frame_step",
    "name",
    "index",
    "use_disk_cache",
    "use_external",
    "use_library_path",
    "filepath",
}
_CORRECTIVE_SMOOTH_FIELDS = {
    "factor",
    "iterations",
    "scale",
    "rest_source",
    "smooth_type",
    "use_only_smooth",
    "use_pin_boundary",
    "vertex_group",
}
_SUBDIVISION_FIELDS = {
    "levels",
    "render_levels",
    "quality",
    "subdivision_type",
    "uv_smooth",
    "use_creases",
}
_SOLIDIFY_FIELDS = {
    "thickness",
    "offset",
    "material_offset",
    "material_offset_rim",
    "use_even_offset",
    "use_quality_normals",
    "use_rim",
}
_WEIGHTED_NORMAL_FIELDS = {"weight", "mode", "thresh", "keep_sharp", "use_face_influence"}
_EXPORT_UNIT_METERS = {
    "METERS": 1.0,
    "CENTIMETERS": 0.01,
    "MILLIMETERS": 0.001,
}
_ANIMATABLE_FIELDS = {
    "CLOTH_SETTINGS": {
        "uniform_pressure_force",
        "target_volume",
        "pressure_factor",
        "fluid_density",
        "shrink_min",
        "shrink_max",
        "pin_stiffness",
        "goal_min",
        "goal_max",
        "goal_default",
        "goal_spring",
        "goal_friction",
        "time_scale",
        "gravity",
    },
    "EFFECTOR_WEIGHTS": _FIELD_WEIGHT_FIELDS - {"apply_to_hair_growing"},
    "FIELD_SETTINGS": {"strength"},
    "COLLIDER_SETTINGS": {"thickness_outer", "cloth_friction", "damping", "use_culling", "use_normal"},
    "SHAPE_KEY": {"value"},
    "HOOK_MODIFIER": {"strength", "falloff_radius"},
    "ARMATURE_MODIFIER": {"strength"},
    "MESH_DEFORM_MODIFIER": {"strength"},
    "SURFACE_DEFORM_MODIFIER": {"strength"},
    "OBJECT": {"location", "rotation_euler", "rotation_quaternion", "scale"},
}
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
    is_array = getattr(prop, "is_array", False)
    if prop.type in {"FLOAT", "INT"} and not is_array and (value < prop.hard_min or value > prop.hard_max):
        raise ValueError(f"{name}={value} is outside Blender's RNA range [{prop.hard_min}, {prop.hard_max}]")
    if prop.type == "ENUM" and value not in {item.identifier for item in prop.enum_items}:
        raise ValueError(f"Invalid {name}: {value}")
    if is_array and len(value) != prop.array_length:
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
    return point_cache_info(cache)


def _shared_cache_identity(cache):
    """Return only an explicit cache identity that can collide across modifiers."""
    return point_cache_identity(cache)


def _external_cache_path_status(cache):
    resolved = bpy.path.abspath(cache.filepath) if cache.filepath else ""
    return {
        "filepath": cache.filepath,
        "resolved": resolved,
        "valid_directory": bool(resolved and os.path.isdir(resolved)),
    }


def _set_cache_frame_range(cache, frame_start, frame_end):
    """Set an already-validated cache range without transiently inverting it."""
    set_cache_frame_range(cache, frame_start, frame_end)


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


def _tag_owned_component(obj, modifier, role, simulation_id=None, source_mapping=None):
    component_id = uuid.uuid4().hex
    simulation_id = simulation_id or component_id
    property_name = f"{_OWNERSHIP_PREFIX}_component_{component_id}"
    record = {
        "owned": True,
        "simulation_id": simulation_id,
        "role": role,
        "modifier": modifier.name,
        "schema_version": _MCP_SCHEMA_VERSION,
    }
    if source_mapping is not None:
        record["source_mapping"] = source_mapping
    obj[property_name] = json.dumps(record, sort_keys=True)
    return {"object_property": property_name, **record}


def _tag_owned_object(obj, role, simulation_id, source_mapping=None):
    component_id = uuid.uuid4().hex
    property_name = f"{_OWNERSHIP_PREFIX}_component_{component_id}"
    record = {
        "owned": True,
        "simulation_id": simulation_id,
        "role": role,
        "object": obj.name,
        "schema_version": _MCP_SCHEMA_VERSION,
    }
    if source_mapping is not None:
        record["source_mapping"] = source_mapping
    obj[property_name] = json.dumps(record, sort_keys=True)
    return {"object_property": property_name, **record}


def _remove_custom_property(obj, property_name):
    if property_name in obj:
        del obj[property_name]


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


def _surface_report(obj):
    if not obj.data.vertices or not obj.data.polygons:
        raise ValueError(f"Mesh '{obj.name}' must contain vertices and faces")
    edge_uses = Counter()
    directed = Counter()
    signed_volume = 0.0
    center = sum((vertex.co for vertex in obj.data.vertices), obj.data.vertices[0].co * 0.0) / max(
        len(obj.data.vertices), 1
    )
    inward_faces = []
    for polygon in obj.data.polygons:
        vertices = list(polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge_uses[tuple(sorted((first, second)))] += 1
            directed[first, second] += 1
        if len(vertices) >= 3:
            origin = obj.data.vertices[vertices[0]].co
            for index in range(1, len(vertices) - 1):
                second = obj.data.vertices[vertices[index]].co
                third = obj.data.vertices[vertices[index + 1]].co
                signed_volume += float(origin.dot(second.cross(third))) / 6.0
        if polygon.area > 1e-12 and polygon.normal.dot(polygon.center - center) < 0:
            inward_faces.append(polygon.index)
    loose_edges = sum(edge_uses.get(tuple(sorted(edge.vertices)), 0) == 0 for edge in obj.data.edges)
    inconsistent = sum(
        directed[second, first] == 0 for first, second in directed if edge_uses[tuple(sorted((first, second)))] == 2
    )
    return {
        "signed_volume_object_local_cubed": signed_volume,
        "absolute_volume_object_local_cubed": abs(signed_volume),
        "boundary_edges": sum(count == 1 for count in edge_uses.values()),
        "non_manifold_edges": sum(count != 2 for count in edge_uses.values()) + loose_edges,
        "loose_edges": loose_edges,
        "inconsistent_winding_edges": inconsistent,
        "inward_face_candidates": inward_faces[:100],
        "inward_face_candidates_truncated": len(inward_faces) > 100,
        "orientation_evidence": "POSITIVE_SIGNED_VOLUME" if signed_volume > 0 else "NON_POSITIVE_SIGNED_VOLUME",
    }


def _sewing_plan(obj, seam_pairs, max_pair_distance):
    if not seam_pairs or len(seam_pairs) > 5_000:
        raise ValueError("seam_pairs must contain 1-5000 explicit pairs")
    vertex_count = len(obj.data.vertices)
    edge_face_uses = Counter()
    for polygon in obj.data.polygons:
        vertices = list(polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge_face_uses[tuple(sorted((first, second)))] += 1
    boundary_vertices = {vertex for edge, uses in edge_face_uses.items() if uses == 1 for vertex in edge}
    mesh_edges = {}
    for edge in obj.data.edges:
        mesh_edges.setdefault(tuple(sorted(edge.vertices)), []).append(edge.index)
    seen = set()
    endpoint_uses = Counter()
    records = []
    connector_vectors = []
    for pair_index, pair in enumerate(seam_pairs):
        first = int(pair["source_vertex"])
        second = int(pair["target_vertex"])
        if first == second:
            raise ValueError(f"Sewing pair {pair_index} repeats vertex {first}")
        if not 0 <= first < vertex_count or not 0 <= second < vertex_count:
            raise ValueError(f"Sewing pair {pair_index} contains an index outside [0, {vertex_count - 1}]")
        key = tuple(sorted((first, second)))
        if key in seen:
            raise ValueError(f"Duplicate sewing pair for vertices {list(key)}")
        seen.add(key)
        endpoint_uses.update((first, second))
        distance = float((obj.data.vertices[first].co - obj.data.vertices[second].co).length)
        if max_pair_distance is not None and distance > max_pair_distance:
            raise ValueError(
                f"Sewing pair {pair_index} distance {distance:g} exceeds max_pair_distance {max_pair_distance:g}"
            )
        face_uses = edge_face_uses.get(key, 0)
        if face_uses:
            raise ValueError(f"Edge {list(key)} belongs to {face_uses} face(s) and is not a loose sewing edge")
        connector_vectors.append(obj.data.vertices[second].co - obj.data.vertices[first].co)
        records.append(
            {
                "pair_index": pair_index,
                "vertices": [first, second],
                "distance_object_local": distance,
                "source_is_boundary": first in boundary_vertices,
                "target_is_boundary": second in boundary_vertices,
                "existing_loose_edge": key in mesh_edges,
                "edge_indices": mesh_edges.get(key, []),
                "duplicate_existing_edges": len(mesh_edges.get(key, [])) > 1,
            }
        )
    reversals = []
    for index, (previous, current) in enumerate(pairwise(connector_vectors), start=1):
        if previous.length_squared and current.length_squared and previous.dot(current) < 0:
            reversals.append(index)
    distances = [record["distance_object_local"] for record in records]
    unused_boundary_vertices = sorted(boundary_vertices - endpoint_uses.keys())
    return {
        "pairs": records,
        "existing_loose_edges": sum(record["existing_loose_edge"] for record in records),
        "missing_loose_edges": sum(not record["existing_loose_edge"] for record in records),
        "duplicate_requested_mesh_edges": sum(record["duplicate_existing_edges"] for record in records),
        "non_boundary_endpoints": sum(
            not record["source_is_boundary"] or not record["target_is_boundary"] for record in records
        ),
        "boundary_vertices": len(boundary_vertices),
        "unmatched_boundary_vertices": len(unused_boundary_vertices),
        "unmatched_boundary_vertex_sample": unused_boundary_vertices[:100],
        "unmatched_boundary_vertices_truncated": len(unused_boundary_vertices) > 100,
        "multiply_mapped_boundary_vertices": sorted(vertex for vertex, uses in endpoint_uses.items() if uses > 1)[:100],
        "pair_distance": {
            "minimum": min(distances),
            "maximum": max(distances),
            "mean": statistics.fmean(distances),
        },
        "direction_reversal_pair_indices": reversals,
        "likely_fold": bool(reversals),
    }


def _set_loose_edges(obj, vertex_pairs, *, create):
    created_keys = []
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            result = bpy.ops.object.mode_set(mode="OBJECT")
            if "FINISHED" not in result:
                raise RuntimeError(f"Could not enter Object Mode for sewing topology: {sorted(result)}")
        mesh = obj.data
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.verts.ensure_lookup_table()
            for first, second in vertex_pairs:
                first_vertex = bm.verts[first]
                second_vertex = bm.verts[second]
                if first_vertex is None or second_vertex is None:
                    raise RuntimeError("BMesh vertex lookup failed after validated sewing preflight")
                vertices = (first_vertex, second_vertex)
                if bm.edges.get(vertices) is None:
                    if not create:
                        continue
                    bm.edges.new(vertices)
                    created_keys.append(tuple(sorted((first, second))))
            if created_keys:
                bm.to_mesh(mesh)
                mesh.update()
        finally:
            bm.free()
    return created_keys


def _remove_edges_by_vertices(obj, vertex_pairs):
    if not vertex_pairs:
        return
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            edges = []
            for first, second in vertex_pairs:
                first_vertex = bm.verts[first]
                second_vertex = bm.verts[second]
                if first_vertex is not None and second_vertex is not None:
                    edges.append(bm.edges.get((first_vertex, second_vertex)))
            bmesh.ops.delete(bm, geom=[edge for edge in edges if edge is not None], context="EDGES")
            bm.to_mesh(obj.data)
            obj.data.update()
        finally:
            bm.free()


def _owned_component_records(obj):
    records = []
    for key, value in obj.items():
        if not key.startswith(f"{_OWNERSHIP_PREFIX}_component_"):
            continue
        with contextlib.suppress(TypeError, json.JSONDecodeError):
            records.append({"object_property": key, **json.loads(value)})
    return records


def _remove_owned_component_record(obj, role, modifier_name):
    for record in _owned_component_records(obj):
        if record.get("role") == role and record.get("modifier") == modifier_name:
            del obj[record["object_property"]]
            return record
    return None


def _tag_owned_membership(obj, collection, simulation_id=None):
    simulation_id = simulation_id or uuid.uuid4().hex
    property_name = f"{_OWNERSHIP_PREFIX}_component_{simulation_id}"
    record = {
        "owned": True,
        "simulation_id": simulation_id,
        "role": "collision_membership",
        "collection": collection.name,
        "schema_version": _MCP_SCHEMA_VERSION,
    }
    obj[property_name] = json.dumps(record, sort_keys=True)
    return {"object_property": property_name, **record}


def _owned_membership_record(obj, collection_name):
    return next(
        (
            record
            for record in _owned_component_records(obj)
            if record.get("role") == "collision_membership" and record.get("collection") == collection_name
        ),
        None,
    )


def _scene_context_for_object(obj):
    scenes = _object_scenes(obj)
    if not scenes:
        raise ValueError(f"Object '{obj.name}' is not linked to a scene")
    scene = bpy.context.scene if bpy.context.scene in scenes else scenes[0]
    for layer in scene.view_layers:
        layer.update()
    view_layer = next((layer for layer in scene.view_layers if obj.name in layer.objects), None)
    if view_layer is None:
        raise ValueError(f"Object '{obj.name}' is excluded from every view layer in scene '{scene.name}'")
    return scene, view_layer


def _move_modifier_immediately_before(obj, modifier, following_modifier):
    current = list(obj.modifiers).index(modifier)
    following = list(obj.modifiers).index(following_modifier)
    target = following - 1 if current < following else following
    if current != target:
        obj.modifiers.move(current, target)
    if list(obj.modifiers).index(modifier) + 1 != list(obj.modifiers).index(following_modifier):
        raise RuntimeError(f"Could not place modifier '{modifier.name}' immediately before '{following_modifier.name}'")


def _move_modifier_immediately_after(obj, modifier, preceding_modifier):
    current = list(obj.modifiers).index(modifier)
    preceding = list(obj.modifiers).index(preceding_modifier)
    target = preceding if current < preceding else preceding + 1
    if current != target:
        obj.modifiers.move(current, target)
    if list(obj.modifiers).index(modifier) != list(obj.modifiers).index(preceding_modifier) + 1:
        raise RuntimeError(f"Could not place modifier '{modifier.name}' immediately after '{preceding_modifier.name}'")


def _attachment_target_matrix(target, bone_name=None):
    target_matrix = target.matrix_world.copy()
    if bone_name:
        pose_bone = target.pose.bones.get(bone_name) if target.pose else None
        if pose_bone is None:
            raise ValueError(f"Pose bone not found: {bone_name}")
        target_matrix = target_matrix @ pose_bone.matrix
    if abs(float(target_matrix.determinant())) <= 1e-12:
        raise ValueError(f"Attachment target '{target.name}' has a singular evaluated transform")
    return target_matrix


def _snapshot_attachment_modifier(modifier):
    fields = {
        "HOOK": ("object", "subtarget", "vertex_group", "matrix_inverse", "center"),
        "ARMATURE": ("object", "vertex_group", "use_vertex_groups"),
        "MESH_DEFORM": ("object", "vertex_group"),
        "SURFACE_DEFORM": ("target", "vertex_group"),
    }[modifier.type]
    snapshot = {}
    for name in fields:
        value = getattr(modifier, name)
        snapshot[name] = value.copy() if hasattr(value, "copy") else value
    return snapshot


def _restore_attachment_modifier(modifier, snapshot):
    for name, value in snapshot.items():
        with contextlib.suppress(Exception):
            setattr(modifier, name, value)


def _bind_deform_modifier(obj, modifier):
    operator = {
        "MESH_DEFORM": bpy.ops.object.meshdeform_bind,
        "SURFACE_DEFORM": bpy.ops.object.surfacedeform_bind,
    }[modifier.type]
    scene, view_layer = _scene_context_for_object(obj)
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            result = bpy.ops.object.mode_set(mode="OBJECT")
            if "FINISHED" not in result:
                raise RuntimeError(f"Could not enter Object Mode for binding: {sorted(result)}")
        with bpy.context.temp_override(
            scene=scene,
            view_layer=view_layer,
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            result = operator(modifier=modifier.name)
    if "FINISHED" not in result or not modifier.is_bound:
        raise RuntimeError(f"Blender did not bind {modifier.type} modifier '{modifier.name}': {sorted(result)}")


def _unbind_deform_modifier(obj, modifier):
    if not modifier.is_bound:
        return
    operator = {
        "MESH_DEFORM": bpy.ops.object.meshdeform_bind,
        "SURFACE_DEFORM": bpy.ops.object.surfacedeform_bind,
    }[modifier.type]
    scene, view_layer = _scene_context_for_object(obj)
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            result = bpy.ops.object.mode_set(mode="OBJECT")
            if "FINISHED" not in result:
                raise RuntimeError(f"Could not enter Object Mode for unbinding: {sorted(result)}")
        with bpy.context.temp_override(
            scene=scene,
            view_layer=view_layer,
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            result = operator(modifier=modifier.name)
    if "FINISHED" not in result or modifier.is_bound:
        raise RuntimeError(f"Blender did not unbind {modifier.type} modifier '{modifier.name}': {sorted(result)}")


def _bind_corrective_smooth(obj, modifier):
    if modifier.is_bind:
        return
    scene, view_layer = _scene_context_for_object(obj)
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            result = bpy.ops.object.mode_set(mode="OBJECT")
            if "FINISHED" not in result:
                raise RuntimeError(f"Could not enter Object Mode for Corrective Smooth binding: {sorted(result)}")
        with bpy.context.temp_override(
            scene=scene,
            view_layer=view_layer,
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            result = bpy.ops.object.correctivesmooth_bind(modifier=modifier.name)
    if "FINISHED" not in result or not modifier.is_bind:
        raise RuntimeError(f"Blender did not bind Corrective Smooth modifier '{modifier.name}': {sorted(result)}")


def _unbind_corrective_smooth(obj, modifier):
    if not modifier.is_bind:
        return
    scene, view_layer = _scene_context_for_object(obj)
    with preserve_mode_and_selection():
        set_active(obj)
        with bpy.context.temp_override(
            scene=scene,
            view_layer=view_layer,
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            result = bpy.ops.object.correctivesmooth_bind(modifier=modifier.name)
    if "FINISHED" not in result or modifier.is_bind:
        raise RuntimeError(f"Blender did not unbind Corrective Smooth modifier '{modifier.name}': {sorted(result)}")


def _sample_indices(count, limit):
    if count <= limit:
        return list(range(count))
    step = count / limit
    return sorted({min(count - 1, int(index * step)) for index in range(limit)})


def _evaluated_world_vertices(obj, limit, depsgraph=None):
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        indices = _sample_indices(len(mesh.vertices), limit)
        return {
            "total": len(mesh.vertices),
            "indices": indices,
            "positions": [evaluated.matrix_world @ mesh.vertices[index].co for index in indices],
        }
    finally:
        evaluated.to_mesh_clear()


def _world_bounds(evaluated_obj):
    corners = [evaluated_obj.matrix_world @ mathutils.Vector(corner) for corner in evaluated_obj.bound_box]
    return {
        "coordinate_space": "WORLD",
        "minimum": [min(corner[axis] for corner in corners) for axis in range(3)],
        "maximum": [max(corner[axis] for corner in corners) for axis in range(3)],
    }


def _evaluated_surface_measurements(evaluated_obj, mesh, polygon_limit):
    matrix = evaluated_obj.matrix_world
    scanned = min(len(mesh.polygons), polygon_limit)
    area = 0.0
    signed_volume = 0.0
    degenerate = []
    for polygon in list(mesh.polygons)[:scanned]:
        indices = list(polygon.vertices)
        if len(indices) < 3:
            degenerate.append(polygon.index)
            continue
        origin = matrix @ mesh.vertices[indices[0]].co
        polygon_area = 0.0
        for index in range(1, len(indices) - 1):
            second = matrix @ mesh.vertices[indices[index]].co
            third = matrix @ mesh.vertices[indices[index + 1]].co
            cross = (second - origin).cross(third - origin)
            polygon_area += cross.length * 0.5
            signed_volume += float(origin.dot(second.cross(third))) / 6.0
        area += polygon_area
        if polygon_area <= 1e-12:
            degenerate.append(polygon.index)
    complete = scanned == len(mesh.polygons)
    return {
        "surface_area_world_squared": area if complete else None,
        "signed_volume_world_cubed": signed_volume if complete else None,
        "polygons_scanned": scanned,
        "total_polygons": len(mesh.polygons),
        "complete": complete,
        "degenerate_face_count_scanned": len(degenerate),
        "degenerate_face_indices_sample": degenerate[:100],
    }


def _collider_proximity(sample_positions, colliders, face_limit, depsgraph=None):
    from mathutils.bvhtree import BVHTree

    evidence = []
    for collider in colliders:
        depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
        evaluated = collider.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            if len(mesh.polygons) > face_limit:
                evidence.append(
                    {
                        "collider": collider.name,
                        "skipped": True,
                        "reason": "evaluated_face_limit",
                        "evaluated_faces": len(mesh.polygons),
                        "face_limit": face_limit,
                    }
                )
                continue
            vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
            polygons = [list(polygon.vertices) for polygon in mesh.polygons]
            bvh = BVHTree.FromPolygons(vertices, polygons, all_triangles=False, epsilon=0.0)
            distances = []
            behind_surface = 0
            for position in sample_positions:
                location, normal, _face_index, distance = bvh.find_nearest(position)
                if location is None or distance is None:
                    continue
                distances.append(float(distance))
                if normal is not None and (position - location).dot(normal) < 0:
                    behind_surface += 1
            evidence.append(
                {
                    "collider": collider.name,
                    "skipped": False,
                    "samples_checked": len(distances),
                    "minimum_surface_distance_world": min(distances) if distances else None,
                    "mean_surface_distance_world": statistics.fmean(distances) if distances else None,
                    "behind_nearest_surface_normal": behind_surface,
                    "heuristic_only": True,
                }
            )
        finally:
            evaluated.to_mesh_clear()
    return evidence


def _evaluated_bvh_overlap(first, second, depsgraph, face_limit=100_000):
    from mathutils.bvhtree import BVHTree

    evaluated_first = first.evaluated_get(depsgraph)
    evaluated_second = second.evaluated_get(depsgraph)
    first_mesh = evaluated_first.to_mesh()
    second_mesh = evaluated_second.to_mesh()
    try:
        if len(first_mesh.polygons) > face_limit or len(second_mesh.polygons) > face_limit:
            return {
                "checked": False,
                "reason": "evaluated_face_limit",
                "face_limit": face_limit,
                "faces": [len(first_mesh.polygons), len(second_mesh.polygons)],
            }
        first_bvh = BVHTree.FromPolygons(
            [evaluated_first.matrix_world @ vertex.co for vertex in first_mesh.vertices],
            [list(polygon.vertices) for polygon in first_mesh.polygons],
            all_triangles=False,
            epsilon=0.0,
        )
        second_bvh = BVHTree.FromPolygons(
            [evaluated_second.matrix_world @ vertex.co for vertex in second_mesh.vertices],
            [list(polygon.vertices) for polygon in second_mesh.polygons],
            all_triangles=False,
            epsilon=0.0,
        )
        overlaps = first_bvh.overlap(second_bvh)
        return {
            "checked": True,
            "coordinate_space": "WORLD",
            "geometry": "EVALUATED_AT_REST_FRAME",
            "overlapping_face_pairs": len(overlaps),
            "sample": [list(pair) for pair in overlaps[:20]],
            "sample_truncated": len(overlaps) > 20,
        }
    finally:
        evaluated_first.to_mesh_clear()
        evaluated_second.to_mesh_clear()


def _external_directory_evidence(filepath):
    resolved = bpy.path.abspath(filepath) if filepath else ""
    exists = bool(resolved and os.path.isdir(resolved))
    entries = []
    truncated = False
    if exists:
        with os.scandir(resolved) as iterator:
            for entry in iterator:
                entries.append(entry.name)
                if len(entries) >= 100:
                    truncated = True
                    break
    return {
        "filepath": filepath,
        "resolved": resolved,
        "exists": exists,
        "writable": bool(exists and os.access(resolved, os.W_OK)),
        "entries": entries,
        "entries_truncated": truncated,
    }


def _all_cloth_caches():
    return [
        (obj, modifier, modifier.point_cache)
        for obj in bpy.data.objects
        for modifier in obj.modifiers
        if modifier.type == "CLOTH"
    ]


def _cloth_cache_dependency_issues(obj, modifier):
    issues = []
    if not obj.data.vertices or not obj.data.edges:
        issues.append("cloth mesh has empty vertex or edge topology")
    settings = modifier.settings
    collisions = modifier.collision_settings
    for field in (
        "vertex_group_mass",
        "vertex_group_structural_stiffness",
        "vertex_group_shear_stiffness",
        "vertex_group_bending",
        "vertex_group_shrink",
        "vertex_group_pressure",
        "vertex_group_intern",
    ):
        group_name = getattr(settings, field, "")
        if group_name and obj.vertex_groups.get(group_name) is None:
            issues.append(f"missing vertex group {field}='{group_name}'")
    for field in ("vertex_group_object_collisions", "vertex_group_self_collisions"):
        group_name = getattr(collisions, field, "")
        if group_name and obj.vertex_groups.get(group_name) is None:
            issues.append(f"missing collision vertex group {field}='{group_name}'")
    scenes = _object_scenes(obj)
    if collisions.collection and not any(_collection_in_scene(collisions.collection, scene) for scene in scenes):
        issues.append(f"collision collection '{collisions.collection.name}' is not linked to a cloth scene")
    effector_collection = settings.effector_weights.collection
    if effector_collection and not any(_collection_in_scene(effector_collection, scene) for scene in scenes):
        issues.append(f"effector collection '{effector_collection.name}' is not linked to a cloth scene")
    return issues


def _cloths_depending_on_object(target):
    affected = {}
    for candidate in bpy.data.objects:
        attachment_indices = []
        for index, modifier in enumerate(candidate.modifiers):
            referenced = getattr(modifier, "object", None) == target or getattr(modifier, "target", None) == target
            if referenced and modifier.type in {"HOOK", "ARMATURE", "MESH_DEFORM", "SURFACE_DEFORM"}:
                attachment_indices.append(index)
        for attachment_index in attachment_indices:
            for cloth_modifier in list(candidate.modifiers)[attachment_index + 1 :]:
                if cloth_modifier.type == "CLOTH":
                    affected[candidate.name, cloth_modifier.name] = (candidate, cloth_modifier)
    return list(affected.values())


def _cloths_affected_by_effector(effector):
    affected = {}
    for scene in _object_scenes(effector):
        for candidate in scene.objects:
            for modifier in candidate.modifiers:
                if modifier.type != "CLOTH":
                    continue
                collection = modifier.settings.effector_weights.collection
                if collection is None or effector.name in collection.all_objects:
                    affected[candidate.name, modifier.name] = (candidate, modifier)
    return list(affected.values())


def _prospective_cache_identity(cache, patch):
    use_external = patch.get("use_external", cache.use_external)
    filepath = patch.get("filepath", cache.filepath)
    if not use_external or not filepath:
        return None
    name = patch.get("name", cache.name)
    index = patch.get("index", cache.index)
    return (
        "EXTERNAL",
        os.path.normcase(os.path.normpath(bpy.path.abspath(filepath))),
        str(name),
        int(index),
    )


def _point_cache_context(obj, cache):
    scene, view_layer = _scene_context_for_object(obj)
    return (
        scene,
        view_layer,
        {
            "scene": scene,
            "view_layer": view_layer,
            "object": obj,
            "active_object": obj,
            "selected_objects": [obj],
            "selected_editable_objects": [obj],
            "point_cache": cache,
        },
    )


def _run_point_cache_operator(obj, cache, operator, **kwargs):
    _scene, _view_layer, override = _point_cache_context(obj, cache)
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            result = bpy.ops.object.mode_set(mode="OBJECT")
            if "FINISHED" not in result:
                raise RuntimeError(f"Could not enter Object Mode for point cache: {sorted(result)}")
        with bpy.context.temp_override(**override):
            result = operator(**kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(
            f"Point-cache operator did not finish: {sorted(result)}; current state={json.dumps(_cache_info(cache))}"
        )
    return result


def _modifier_driver_paths(obj, modifier):
    with contextlib.suppress(Exception):
        prefix = modifier.path_from_id()
        animation = getattr(obj, "animation_data", None)
        return [curve.data_path for curve in getattr(animation, "drivers", ()) if curve.data_path.startswith(prefix)]
    return []


def _resolve_animation_owner(obj, cloth_modifier_name, record):
    owner_kind = record["owner"]
    target_name = record.get("target_name")
    cloth_modifier = None
    if owner_kind in {"CLOTH_SETTINGS", "EFFECTOR_WEIGHTS"}:
        if not cloth_modifier_name:
            raise ValueError(f"cloth_modifier_name is required for {owner_kind}")
        cloth_modifier = _get_modifier(obj, cloth_modifier_name, "CLOTH")
        owner = cloth_modifier.settings if owner_kind == "CLOTH_SETTINGS" else cloth_modifier.settings.effector_weights
        allowlist = _ANIMATABLE_FIELDS[owner_kind]
    elif owner_kind == "COLLIDER_SETTINGS":
        if obj.collision is None or not any(modifier.type == "COLLISION" for modifier in obj.modifiers):
            raise ValueError(f"Object '{obj.name}' is not a cloth collider")
        owner = obj.collision
        allowlist = _ANIMATABLE_FIELDS[owner_kind]
    elif owner_kind == "FIELD_SETTINGS":
        owner = obj.field
        if owner is None or owner.type == "NONE":
            raise ValueError(f"Object '{obj.name}' is not an active force field")
        allowlist = _ANIMATABLE_FIELDS[owner_kind]
    elif owner_kind == "SHAPE_KEY":
        shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
        owner = shape_keys.key_blocks.get(target_name) if shape_keys and target_name else None
        if owner is None:
            raise ValueError(f"Shape key not found: {target_name}")
        allowlist = _ANIMATABLE_FIELDS[owner_kind]
    elif owner_kind == "MODIFIER":
        if not target_name:
            raise ValueError("target_name must name an attachment modifier")
        owner = obj.modifiers.get(target_name)
        if owner is None:
            raise ValueError(f"Modifier not found: {target_name}")
        key = f"{owner.type}_MODIFIER"
        allowlist = _ANIMATABLE_FIELDS.get(key)
        if allowlist is None:
            raise ValueError(f"Modifier type {owner.type} is not animatable through this tool")
    elif owner_kind == "OBJECT":
        owner = obj
        allowlist = _ANIMATABLE_FIELDS[owner_kind]
    else:
        raise ValueError(f"Unsupported animation owner: {owner_kind}")
    property_name = record["property_name"]
    if property_name not in allowlist:
        raise ValueError(f"Property '{property_name}' is not allowed for {owner_kind}")
    prop = _rna_property(owner, property_name)
    if not prop.is_animatable:
        raise ValueError(f"{owner_kind}.{property_name} is not animatable in Blender {bpy.app.version_string}")
    value = record["value"]
    array_index = record.get("array_index", -1)
    if prop.is_array:
        if array_index == -1:
            _validate_rna_value(owner, property_name, value)
        elif not isinstance(value, (int, float)):
            raise ValueError(f"Indexed animation of {property_name} requires one numeric value")
        elif not 0 <= array_index < prop.array_length:
            raise ValueError(f"array_index must be in [0, {prop.array_length - 1}] for {property_name}")
    else:
        if array_index != -1:
            raise ValueError(f"array_index is not valid for scalar property {property_name}")
        _validate_rna_value(owner, property_name, value)
    path = owner.path_from_id(property_name)
    return owner, property_name, path, cloth_modifier


def _keyframe_points(owner, data_path, array_index, frame):
    owner_id = getattr(owner, "id_data", owner)
    animation = getattr(owner_id, "animation_data", None)
    matches = []
    for curve in _action_fcurves(animation):
        if curve.data_path != data_path or (array_index >= 0 and curve.array_index != array_index):
            continue
        for point in curve.keyframe_points:
            if abs(float(point.co[0]) - frame) <= 1e-6:
                matches.append((curve, point))
    return matches


def _set_animated_property(owner, property_name, value, array_index):
    if array_index >= 0:
        values = list(getattr(owner, property_name))
        values[array_index] = value
        setattr(owner, property_name, values)
    else:
        setattr(owner, property_name, value)


def _snapshot_keyframe_point(point):
    return {
        "co": list(point.co),
        "interpolation": point.interpolation,
        "easing": point.easing,
        "handle_left": list(point.handle_left),
        "handle_right": list(point.handle_right),
        "handle_left_type": point.handle_left_type,
        "handle_right_type": point.handle_right_type,
    }


def _restore_keyframe_point(point, snapshot):
    point.co = snapshot["co"]
    point.interpolation = snapshot["interpolation"]
    point.easing = snapshot["easing"]
    point.handle_left = snapshot["handle_left"]
    point.handle_right = snapshot["handle_right"]
    point.handle_left_type = snapshot["handle_left_type"]
    point.handle_right_type = snapshot["handle_right_type"]


def _validate_distinct_axes(forward_axis, up_axis):
    forward = forward_axis.removeprefix("NEGATIVE_")
    up = up_axis.removeprefix("NEGATIVE_")
    if forward == up:
        raise ValueError("forward_axis and up_axis must use different axes")


def _validate_frames(frames, *, maximum, label="frames"):
    if not frames:
        raise ValueError(f"{label} must contain at least one frame")
    if len(frames) > maximum:
        raise ValueError(f"{label} exceeds the maximum of {maximum}")
    normalized = [int(frame) for frame in frames]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must not contain duplicates")
    return sorted(normalized)


def _validate_id_name(name, label):
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{label} must be a nonempty name")
    if len(name.encode("utf-8")) > 63:
        raise ValueError(f"{label} exceeds Blender's 63-byte ID name limit")
    return name


def _copy_animation_action(source, duplicate, policy):
    animation = getattr(duplicate, "animation_data", None)
    action = getattr(animation, "action", None)
    if action is None:
        return None
    if policy == "COPY":
        copied = action.copy()
        copied.name = f"{source.name} Variant Action"
        animation.action = copied
        return copied
    return action


def _copy_data_actions(duplicate, policy):
    if policy != "COPY" or duplicate.data is None:
        return []
    copied = []
    owners = [duplicate.data, getattr(duplicate.data, "shape_keys", None)]
    for owner in owners:
        animation = getattr(owner, "animation_data", None)
        action = getattr(animation, "action", None)
        if action is None:
            continue
        action_copy = action.copy()
        action_copy.name = f"{action.name} Variant"
        animation.action = action_copy
        copied.append(action_copy)
    return copied


def _copy_mesh_materials(mesh):
    copied = []
    for index, material in enumerate(list(mesh.materials)):
        if material is None:
            continue
        duplicate = material.copy()
        duplicate.name = f"{material.name} Variant"
        mesh.materials[index] = duplicate
        copied.append(duplicate)
    return copied


def _duplicate_object(source, name, collection, *, copy_mesh, material_policy, animation_policy):
    duplicate = source.copy()
    duplicate.name = name
    copied_data = None
    copied_materials = []
    if source.data is not None and copy_mesh:
        copied_data = source.data.copy()
        duplicate.data = copied_data
        if source.type == "MESH" and material_policy == "COPY":
            copied_materials = _copy_mesh_materials(copied_data)
    elif material_policy == "COPY":
        raise ValueError("material_policy=COPY requires mesh_data_policy=COPY")
    copied_actions = []
    copied_action = _copy_animation_action(source, duplicate, animation_policy)
    if copied_action is not None and copied_action != getattr(getattr(source, "animation_data", None), "action", None):
        copied_actions.append(copied_action)
    if copied_data is not None:
        copied_actions.extend(_copy_data_actions(duplicate, animation_policy))
    collection.objects.link(duplicate)
    return duplicate, copied_data, copied_materials, copied_actions


def _remove_created_object(obj, copied_data=None, copied_materials=(), copied_actions=()):
    for collection in list(obj.users_collection):
        with contextlib.suppress(Exception):
            collection.objects.unlink(obj)
    with contextlib.suppress(Exception):
        bpy.data.objects.remove(obj)
    if copied_data is not None and getattr(copied_data, "users", 1) == 0:
        with contextlib.suppress(Exception):
            bpy.data.batch_remove(ids=[copied_data])
    for material in copied_materials:
        if getattr(material, "users", 1) == 0:
            with contextlib.suppress(Exception):
                bpy.data.materials.remove(material)
    for action in copied_actions:
        if getattr(action, "users", 1) == 0:
            with contextlib.suppress(Exception):
                bpy.data.actions.remove(action)


def _apply_named_modifier(obj, modifier):
    scene, view_layer = _scene_context_for_object(obj)
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            result = bpy.ops.object.mode_set(mode="OBJECT")
            if "FINISHED" not in result:
                raise RuntimeError(f"Could not enter Object Mode for modifier application: {sorted(result)}")
        with bpy.context.temp_override(
            scene=scene,
            view_layer=view_layer,
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            result = bpy.ops.object.modifier_apply(modifier=modifier.name)
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender did not apply modifier '{modifier.name}': {sorted(result)}")


def _evaluated_geometry_evidence(obj, depsgraph=None):
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "bounds": _world_bounds(evaluated),
        }
    finally:
        evaluated.to_mesh_clear()


def _proxy_proximity_evidence(render_obj, proxy_obj, depsgraph, sample_limit=10_000):
    from mathutils.bvhtree import BVHTree

    evaluated_render = render_obj.evaluated_get(depsgraph)
    evaluated_proxy = proxy_obj.evaluated_get(depsgraph)
    render_mesh = evaluated_render.to_mesh()
    proxy_mesh = evaluated_proxy.to_mesh()
    try:
        if not proxy_mesh.polygons:
            raise ValueError(f"Proxy '{proxy_obj.name}' evaluates without faces")
        proxy_vertices = [evaluated_proxy.matrix_world @ vertex.co for vertex in proxy_mesh.vertices]
        proxy_polygons = [list(polygon.vertices) for polygon in proxy_mesh.polygons]
        tree = BVHTree.FromPolygons(proxy_vertices, proxy_polygons, all_triangles=False, epsilon=0.0)
        distances = []
        missed = 0
        for index in _sample_indices(len(render_mesh.vertices), sample_limit):
            position = evaluated_render.matrix_world @ render_mesh.vertices[index].co
            _location, _normal, _face_index, distance = tree.find_nearest(position)
            if distance is None:
                missed += 1
            else:
                distances.append(float(distance))
        return {
            "coordinate_space": "WORLD",
            "render_vertices": len(render_mesh.vertices),
            "proxy_vertices": len(proxy_mesh.vertices),
            "samples": len(distances),
            "missed_samples": missed,
            "minimum_distance": min(distances) if distances else None,
            "maximum_distance": max(distances) if distances else None,
            "mean_distance": statistics.fmean(distances) if distances else None,
            "sampled": len(render_mesh.vertices) > sample_limit,
        }
    finally:
        evaluated_render.to_mesh_clear()
        evaluated_proxy.to_mesh_clear()


def _configure_independent_cache(
    cache,
    object_name,
    modifier_name,
    cache_directory=None,
    index=0,
    identity_token=None,
):
    token = identity_token or uuid.uuid4().hex[:8]
    cache.name = f"{object_name[:30]}_{modifier_name[:16]}_{token[:8]}"
    cache.index = index
    if cache_directory:
        resolved = bpy.path.abspath(cache_directory)
        if not os.path.isdir(resolved) or not os.access(resolved, os.W_OK):
            raise ValueError(f"cache_directory must be an existing writable directory: {cache_directory}")
        cache.use_external = True
        cache.filepath = cache_directory
    else:
        cache.use_external = False
        cache.use_disk_cache = False
        cache.filepath = ""


def _modifier_dependency_target(modifier):
    if modifier.type in {"SURFACE_DEFORM", "NORMAL_EDIT"}:
        return getattr(modifier, "target", None)
    return getattr(modifier, "object", None)


def _set_modifier_dependency_target(modifier, target):
    if modifier.type in {"SURFACE_DEFORM", "NORMAL_EDIT"}:
        modifier.target = target
    elif hasattr(modifier, "object"):
        modifier.object = target


def _export_frame_topology(objects, scene, view_layer, frames):
    records = []
    for frame in frames:
        scene.frame_set(frame)
        view_layer.update()
        counts = {}
        for obj in objects:
            evaluated = obj.evaluated_get(view_layer.depsgraph)
            mesh = evaluated.to_mesh()
            try:
                digest = hashlib.blake2b(digest_size=16)
                for polygon in mesh.polygons:
                    digest.update(len(polygon.vertices).to_bytes(4, "little"))
                    for vertex_index in polygon.vertices:
                        digest.update(int(vertex_index).to_bytes(8, "little"))
                counts[obj.name] = {
                    "vertices": len(mesh.vertices),
                    "edges": len(mesh.edges),
                    "faces": len(mesh.polygons),
                    "connectivity_digest": digest.hexdigest(),
                }
            finally:
                evaluated.to_mesh_clear()
        records.append({"frame": frame, "counts": counts})
    stable = all(record["counts"] == records[0]["counts"] for record in records[1:])
    return records, stable


def _set_scene_frame_range(scene, frame_start, frame_end, frame_step):
    if frame_start > scene.frame_end:
        scene.frame_end = frame_end
        scene.frame_start = frame_start
    else:
        scene.frame_start = frame_start
        scene.frame_end = frame_end
    scene.frame_step = frame_step
    if (scene.frame_start, scene.frame_end, scene.frame_step) != (frame_start, frame_end, frame_step):
        raise ValueError("Blender did not retain the requested scene frame range")


def _modifier_cost_evidence(obj, cloth_modifier, depsgraph):
    base = {"vertices": len(obj.data.vertices), "edges": len(obj.data.edges), "faces": len(obj.data.polygons)}
    evaluated = _evaluated_geometry_evidence(obj, depsgraph)
    colliders = _eligible_active_colliders(obj, cloth_modifier.collision_settings)
    collider_faces = 0
    collider_records = []
    for collider in colliders:
        evaluated_collider = collider.evaluated_get(depsgraph)
        mesh = evaluated_collider.to_mesh()
        try:
            faces = len(mesh.polygons)
            collider_faces += faces
            collider_records.append({"object": collider.name, "evaluated_faces": faces})
        finally:
            evaluated_collider.to_mesh_clear()
    settings = cloth_modifier.settings
    collisions = cloth_modifier.collision_settings
    return {
        "base_geometry": base,
        "evaluated_geometry": evaluated,
        "constraints_heuristic": len(obj.data.edges),
        "solver_quality": settings.quality,
        "collision_quality": collisions.collision_quality,
        "self_collision": collisions.use_self_collision,
        "pressure": settings.use_pressure,
        "internal_springs": settings.use_internal_springs,
        "colliders": collider_records,
        "collider_evaluated_faces": collider_faces,
        "topology_changing_modifiers": [
            modifier.name for modifier in obj.modifiers if modifier.type in _TOPOLOGY_MODIFIERS
        ],
        "modifier_execution_seconds": {
            modifier.name: float(modifier.execution_time)
            for modifier in obj.modifiers
            if hasattr(modifier, "execution_time")
        },
    }


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
        membership_ownership = []
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
                    membership_ownership.append(_tag_owned_membership(obj, collection))
                cloth_mod.collision_settings.collection = collection
            if created:
                ownership = _tag_owned_component(obj, modifier, "collider")
            _tag_update(obj)
        except Exception:
            for record in membership_ownership:
                with contextlib.suppress(Exception):
                    del obj[record["object_property"]]
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
            "membership_ownership": membership_ownership,
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

    def configure_cloth_sewing(
        self,
        object_name,
        modifier_name,
        seam_pairs,
        sewing_force_max,
        create_missing_edges=False,
        dry_run=True,
        max_pair_distance=None,
    ):
        obj, modifier = _get_cloth(object_name, modifier_name)
        sync_from_editmode(obj)
        if max_pair_distance is not None:
            _finite(max_pair_distance, "max_pair_distance")
            if max_pair_distance <= 0:
                raise ValueError("max_pair_distance must be positive")
        _validate_rna_value(modifier.settings, "sewing_force_max", sewing_force_max)
        plan = _sewing_plan(obj, seam_pairs, max_pair_distance)
        if plan["non_boundary_endpoints"]:
            raise ValueError(
                f"{plan['non_boundary_endpoints']} sewing pair(s) contain endpoints outside panel boundaries"
            )
        if dry_run:
            return {
                "changed_objects": [],
                "object": obj.name,
                "modifier": modifier.name,
                "dry_run": True,
                "would_create_edges": plan["missing_loose_edges"] if create_missing_edges else 0,
                "analysis": plan,
                "point_cache": _cache_info(modifier.point_cache),
            }
        _reject_baked([(obj, modifier)])
        if plan["duplicate_requested_mesh_edges"]:
            raise ValueError(
                f"{plan['duplicate_requested_mesh_edges']} requested seam pair(s) already have duplicate mesh edges"
            )
        if plan["missing_loose_edges"] and not create_missing_edges:
            raise ValueError(
                f"{plan['missing_loose_edges']} requested sewing edges do not exist; "
                "set create_missing_edges=True or provide existing loose edges"
            )
        old_settings = {
            "use_sewing_springs": modifier.settings.use_sewing_springs,
            "sewing_force_max": modifier.settings.sewing_force_max,
        }
        missing_pairs = [tuple(record["vertices"]) for record in plan["pairs"] if not record["existing_loose_edge"]]
        created_edges = []
        try:
            created_edges = _set_loose_edges(obj, missing_pairs, create=create_missing_edges)
            modifier.settings.use_sewing_springs = True
            modifier.settings.sewing_force_max = sewing_force_max
            _tag_update(obj)
        except Exception:
            with contextlib.suppress(Exception):
                _remove_edges_by_vertices(obj, created_edges)
            for name, value in old_settings.items():
                with contextlib.suppress(Exception):
                    setattr(modifier.settings, name, value)
            raise
        updated = _sewing_plan(obj, seam_pairs, max_pair_distance)
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "dry_run": False,
            "settings": {
                "use_sewing_springs": {"old": old_settings["use_sewing_springs"], "new": True},
                "sewing_force_max": {
                    "old": old_settings["sewing_force_max"],
                    "new": modifier.settings.sewing_force_max,
                },
            },
            "created_edges": [list(pair) for pair in created_edges],
            "analysis": updated,
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": ["Topology changed; query get_mesh_data again before reusing any mesh indices."]
            if created_edges
            else ["Sewing settings changed and invalidate unbaked simulation state."],
        }

    def configure_cloth_pressure(self, object_name, modifier_name, patch):
        obj, modifier = _get_cloth(object_name, modifier_name)
        sync_from_editmode(obj)
        _reject_baked([(obj, modifier)])
        if not patch:
            raise ValueError("Pressure patch cannot be empty")
        group_name = patch.get("vertex_group_pressure")
        if group_name and obj.vertex_groups.get(group_name) is None:
            raise ValueError(f"Vertex group not found: {group_name}")
        report = _surface_report(obj)
        enabling = patch.get("use_pressure", modifier.settings.use_pressure)
        volume_control = patch.get("use_pressure_volume", modifier.settings.use_pressure_volume)
        target_volume = patch.get("target_volume", modifier.settings.target_volume)
        if enabling:
            if report["non_manifold_edges"]:
                raise ValueError("Pressure requires a closed manifold mesh with no boundary or loose edges")
            if report["inconsistent_winding_edges"]:
                raise ValueError("Pressure requires consistently oriented faces")
            if report["signed_volume_object_local_cubed"] <= 1e-12:
                raise ValueError("Pressure requires outward orientation and positive nonzero signed volume")
            if volume_control and target_volume <= 0:
                raise ValueError("Pressure volume control requires a positive target_volume")
        changes = _patch_rna(modifier.settings, patch, _PRESSURE_FIELDS)
        try:
            _tag_update(obj)
        except Exception:
            _restore_rna(modifier.settings, changes)
            raise
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "surface": report,
            "material_relationship": {
                "tension_stiffness": modifier.settings.tension_stiffness,
                "compression_stiffness": modifier.settings.compression_stiffness,
                "bending_stiffness": modifier.settings.bending_stiffness,
                "pressure_factor": modifier.settings.pressure_factor,
                "uniform_pressure_force": modifier.settings.uniform_pressure_force,
            },
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": ["Pressure settings changed and invalidate unbaked simulation state."],
        }

    def configure_cloth_internal_springs(
        self,
        object_name,
        modifier_name,
        patch,
        max_estimated_springs=2_000_000,
    ):
        obj, modifier = _get_cloth(object_name, modifier_name)
        sync_from_editmode(obj)
        _reject_baked([(obj, modifier)])
        if not patch:
            raise ValueError("Internal-spring patch cannot be empty")
        if not 1 <= max_estimated_springs <= 20_000_000:
            raise ValueError("max_estimated_springs must be in [1, 20000000]")
        group_name = patch.get("vertex_group_intern")
        if group_name and obj.vertex_groups.get(group_name) is None:
            raise ValueError(f"Vertex group not found: {group_name}")
        report = _surface_report(obj)
        enabling = patch.get("use_internal_springs", modifier.settings.use_internal_springs)
        vertex_count = len(obj.data.vertices)
        all_pairs = vertex_count * max(vertex_count - 1, 0) // 2
        max_length = patch.get("internal_spring_max_length", modifier.settings.internal_spring_max_length)
        _finite(max_length, "internal_spring_max_length")
        coordinates = [vertex.co for vertex in obj.data.vertices]
        extents = [max(axis) - min(axis) for axis in zip(*coordinates, strict=False)] if coordinates else [0.0] * 3
        bounds_volume = math.prod(max(float(extent), 1e-12) for extent in extents)
        if max_length > 0 and vertex_count:
            local_density = vertex_count / bounds_volume
            neighborhood = local_density * (4.0 / 3.0) * math.pi * max_length**3
            estimated_pairs = min(all_pairs, math.ceil(vertex_count * neighborhood * 0.5))
        else:
            estimated_pairs = all_pairs
        if enabling:
            if report["non_manifold_edges"] or report["inconsistent_winding_edges"]:
                raise ValueError("Internal springs require closed, consistently oriented volumetric geometry")
            if estimated_pairs > max_estimated_springs:
                raise ValueError(
                    f"Estimated internal-spring candidates {estimated_pairs} exceed "
                    f"max_estimated_springs {max_estimated_springs}; reduce density or maximum length"
                )
        changes = _patch_rna(modifier.settings, patch, _INTERNAL_SPRING_FIELDS)
        try:
            _tag_update(obj)
        except Exception:
            _restore_rna(modifier.settings, changes)
            raise
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "surface": report,
            "spring_estimate": {
                "vertices": vertex_count,
                "absolute_pair_upper_bound": all_pairs,
                "density_length_estimate": estimated_pairs,
                "maximum_length_object_local": max_length,
                "accepted_limit": max_estimated_springs,
                "estimate_only": True,
            },
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": ["Internal-spring settings changed and invalidate unbaked simulation state."],
        }

    def configure_cloth_rest_shape(
        self,
        object_name,
        modifier_name,
        shape_key_name,
        use_dynamic_mesh,
        cache_frame_start,
        cache_frame_end,
    ):
        obj, modifier = _get_cloth(object_name, modifier_name)
        sync_from_editmode(obj)
        _reject_baked([(obj, modifier)])
        shape_keys = getattr(obj.data, "shape_keys", None)
        if shape_keys is None or shape_keys.reference_key is None:
            raise ValueError(f"Mesh '{obj.name}' has no Basis shape key")
        shape_key = shape_keys.key_blocks.get(shape_key_name)
        if shape_key is None:
            raise ValueError(f"Shape key not found: {shape_key_name}")
        if shape_key == shape_keys.reference_key:
            raise ValueError("Choose a non-Basis shape key as the cloth rest shape")
        if len(shape_key.data) != len(obj.data.vertices):
            raise ValueError("Rest shape key vertex count does not match the base mesh")
        if cache_frame_start > cache_frame_end:
            raise ValueError("cache_frame_start must be <= cache_frame_end")
        cache = modifier.point_cache
        _validate_rna_value(cache, "frame_start", cache_frame_start)
        _validate_rna_value(cache, "frame_end", cache_frame_end)
        _validate_rna_value(modifier.settings, "use_dynamic_mesh", use_dynamic_mesh)
        old_shape = modifier.settings.rest_shape_key
        old_dynamic = modifier.settings.use_dynamic_mesh
        old_range = (cache.frame_start, cache.frame_end)
        try:
            modifier.settings.rest_shape_key = shape_key
            modifier.settings.use_dynamic_mesh = use_dynamic_mesh
            _set_cache_frame_range(cache, cache_frame_start, cache_frame_end)
            _tag_update(obj)
        except Exception:
            modifier.settings.rest_shape_key = old_shape
            modifier.settings.use_dynamic_mesh = old_dynamic
            _set_cache_frame_range(cache, *old_range)
            raise
        cloth_index = list(obj.modifiers).index(modifier)
        upstream = list(obj.modifiers)[:cloth_index]
        topology_modifiers = [item.name for item in upstream if item.type in _TOPOLOGY_MODIFIERS]
        upstream_deformers = [item.name for item in upstream if item.type in _DEFORMING_MODIFIERS]
        animated_upstream = [item.name for item in upstream if _modifier_is_animated(obj, item)]
        warnings = []
        if use_dynamic_mesh and topology_modifiers:
            warnings.append(
                f"Dynamic mesh is enabled with upstream topology modifiers {topology_modifiers}; "
                "topology must remain identical throughout the cache range."
            )
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "rest_shape_key": {
                "old": old_shape.name if old_shape else None,
                "new": shape_key.name,
                "vertex_count": len(shape_key.data),
            },
            "use_dynamic_mesh": {"old": old_dynamic, "new": modifier.settings.use_dynamic_mesh},
            "cache_range": {"old": list(old_range), "new": [cache.frame_start, cache.frame_end]},
            "upstream_deformers": upstream_deformers,
            "upstream_topology_modifiers": topology_modifiers,
            "animated_upstream_modifiers": animated_upstream,
            "shape_key_animation": _animation_info(obj),
            "rest_source_intent": (
                "DYNAMIC_PRE_SIMULATION_MESH_WITH_REST_SHAPE_KEY"
                if use_dynamic_mesh
                else "STATIC_SHAPE_KEY_REST_SURFACE"
            ),
            "point_cache": _cache_info(cache),
            "warnings": warnings,
        }

    def configure_cloth_field_weights(self, object_name, modifier_name, patch):
        obj, modifier = _get_cloth(object_name, modifier_name)
        _reject_baked([(obj, modifier)])
        if not patch:
            raise ValueError("Field-weight patch cannot be empty")
        patch = dict(patch)
        collection_name = patch.pop("collection_name", None)
        clear_collection = patch.pop("clear_collection", False)
        if collection_name and clear_collection:
            raise ValueError("collection_name and clear_collection cannot be combined")
        scenes = _object_scenes(obj)
        if not scenes:
            raise ValueError(f"Cloth object '{obj.name}' is not linked to a scene")
        scene = scenes[0]
        collection = None
        if collection_name:
            collection = bpy.data.collections.get(collection_name)
            if collection is None:
                raise ValueError(f"Collection not found: {collection_name}")
            if not _collection_in_scene(collection, scene):
                raise ValueError(f"Effector collection '{collection_name}' is not linked to scene '{scene.name}'")
        weights = modifier.settings.effector_weights
        old_collection = weights.collection
        changes = _patch_rna(weights, patch, _FIELD_WEIGHT_FIELDS)
        try:
            if collection_name or clear_collection:
                weights.collection = collection if collection_name else None
                changes["collection"] = {
                    "old": old_collection.name if old_collection else None,
                    "new": collection.name if collection else None,
                }
            _tag_update(obj)
        except Exception:
            _restore_rna(weights, changes)
            weights.collection = old_collection
            raise
        relationships = _field_relationships(modifier.settings, scene)
        cloth_location = obj.matrix_world.translation
        proximity = []
        threshold = max(float(obj.dimensions.length), 1e-6)
        for item in relationships["effectors"]:
            field_obj = bpy.data.objects.get(item["object"])
            if field_obj is None:
                continue
            distance = float((field_obj.matrix_world.translation - cloth_location).length)
            proximity.append({**item, "origin_distance_world": distance})
        warnings = []
        for item in relationships["effectors"]:
            field_obj = bpy.data.objects.get(item["object"])
            if field_obj and not any(field_obj.name in layer.objects for layer in scene.view_layers):
                warnings.append(f"Force field '{field_obj.name}' is excluded from every scene view layer.")
        close = [item["object"] for item in proximity if item["origin_distance_world"] < threshold]
        if close:
            warnings.append(f"Force-field origins within one cloth bounding-box diagonal: {close}")
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "cloth_gravity_vector": list(modifier.settings.gravity),
            "effector_gravity_multiplier": weights.gravity,
            "combined_gravity_intent": [component * weights.gravity for component in modifier.settings.gravity],
            "field_relationships": {**relationships, "proximity": proximity},
            "point_cache": _cache_info(modifier.point_cache),
            "warnings": warnings,
        }

    def animate_cloth_parameters(
        self,
        object_name,
        keyframes,
        cloth_modifier_name=None,
        policy="INSERT_ONLY",
    ):
        obj = _get_object(object_name)
        if policy not in {"INSERT_ONLY", "REPLACE_EXISTING"}:
            raise ValueError("policy must be INSERT_ONLY or REPLACE_EXISTING")
        if not keyframes or len(keyframes) > 500:
            raise ValueError("keyframes must contain 1-500 records")
        resolved = []
        affected = []
        seen = set()
        for index, record in enumerate(keyframes):
            frame = float(record["frame"])
            if not math.isfinite(frame) or not -1_000_000 <= frame <= 1_000_000:
                raise ValueError(f"Keyframe {index} frame must be finite and in [-1000000, 1000000]")
            owner, property_name, data_path, cloth_modifier = _resolve_animation_owner(obj, cloth_modifier_name, record)
            array_index = int(record.get("array_index", -1))
            identity = (id(owner), data_path, array_index, frame)
            if identity in seen:
                raise ValueError(f"Duplicate keyframe target at record {index}")
            seen.add(identity)
            existing = _keyframe_points(owner, data_path, array_index, frame)
            if policy == "INSERT_ONLY" and existing:
                raise ValueError(f"Key already exists for {record['owner']}.{property_name} at frame {frame:g}")
            if cloth_modifier is not None:
                affected.append((obj, cloth_modifier))
            elif record["owner"] == "COLLIDER_SETTINGS":
                affected.extend(_affected_cloths(obj))
            elif record["owner"] == "FIELD_SETTINGS":
                affected.extend(_cloths_affected_by_effector(obj))
            elif record["owner"] == "OBJECT":
                affected.extend(_cloths_depending_on_object(obj))
                affected.extend(_affected_cloths(obj))
                affected.extend((obj, mod) for mod in obj.modifiers if mod.type == "CLOTH")
            else:
                affected.extend((obj, mod) for mod in obj.modifiers if mod.type == "CLOTH")
            resolved.append(
                {
                    "record": record,
                    "owner": owner,
                    "property_name": property_name,
                    "data_path": data_path,
                    "array_index": array_index,
                    "frame": frame,
                    "old_value": _serialize(getattr(owner, property_name)),
                    "existing": [(curve, point, _snapshot_keyframe_point(point)) for curve, point in existing],
                }
            )
        affected = list({(cloth.name, modifier.name): (cloth, modifier) for cloth, modifier in affected}.values())
        _reject_baked(affected)
        applied = []
        try:
            for entry in resolved:
                record = entry["record"]
                owner = entry["owner"]
                applied.append(entry)
                _set_animated_property(
                    owner,
                    entry["property_name"],
                    record["value"],
                    entry["array_index"],
                )
                inserted = owner.keyframe_insert(
                    data_path=entry["property_name"],
                    index=entry["array_index"],
                    frame=entry["frame"],
                    group="Cloth MCP",
                )
                if not inserted:
                    raise RuntimeError(
                        f"Blender did not insert {record['owner']}.{entry['property_name']} at frame {entry['frame']:g}"
                    )
                points = _keyframe_points(
                    owner,
                    entry["data_path"],
                    entry["array_index"],
                    entry["frame"],
                )
                if not points:
                    raise RuntimeError("Inserted keyframe could not be found in the owner action slot")
                for curve, point in points:
                    point.interpolation = record["interpolation"]
                    curve.update()
            _tag_update(obj)
        except Exception:
            for entry in reversed(applied):
                owner = entry["owner"]
                with contextlib.suppress(Exception):
                    setattr(owner, entry["property_name"], entry["old_value"])
                if entry["existing"]:
                    for curve, point, snapshot in entry["existing"]:
                        with contextlib.suppress(Exception):
                            _restore_keyframe_point(point, snapshot)
                            curve.update()
                else:
                    with contextlib.suppress(Exception):
                        owner.keyframe_delete(
                            data_path=entry["property_name"],
                            index=entry["array_index"],
                            frame=entry["frame"],
                        )
            raise
        keyed = []
        for entry in resolved:
            record = entry["record"]
            owner_id = getattr(entry["owner"], "id_data", entry["owner"])
            animation = getattr(owner_id, "animation_data", None)
            action = getattr(animation, "action", None)
            keyed.append(
                {
                    "owner": record["owner"],
                    "target_name": record.get("target_name"),
                    "property": entry["property_name"],
                    "data_path": entry["data_path"],
                    "array_index": entry["array_index"],
                    "frame": entry["frame"],
                    "value": _serialize(getattr(entry["owner"], entry["property_name"])),
                    "interpolation": record["interpolation"],
                    "action": action.name if action else None,
                    "action_slot": getattr(getattr(animation, "action_slot", None), "identifier", None),
                }
            )
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "policy": policy,
            "keyframes": keyed,
            "affected_cloth_caches": [
                {"object": cloth.name, "modifier": mod.name, "point_cache": _cache_info(mod.point_cache)}
                for cloth, mod in affected
            ],
            "warnings": ["Keyframed simulation dependencies invalidate unbaked cloth state."],
        }

    def create_cloth_attachment(
        self,
        cloth_object_name,
        cloth_modifier_name,
        pin_group_name,
        target_object_name,
        attachment_type="HOOK",
        attachment_modifier_name="Cloth Attachment",
        bone_name=None,
        rest_frame=1,
        existing_policy="ERROR",
        bind=True,
    ):
        cloth, cloth_modifier = _get_cloth(cloth_object_name, cloth_modifier_name)
        sync_from_editmode(cloth)
        _reject_baked([(cloth, cloth_modifier)])
        if existing_policy not in {"ERROR", "REUSE"}:
            raise ValueError("existing_policy must be ERROR or REUSE")
        if attachment_type not in {"HOOK", "ARMATURE", "MESH_DEFORM", "SURFACE_DEFORM"}:
            raise ValueError(f"Unsupported attachment_type: {attachment_type}")
        pin_group = cloth.vertex_groups.get(pin_group_name)
        if pin_group is None:
            raise ValueError(f"Pin vertex group not found: {pin_group_name}")
        pin_stats = _vertex_group_stats(cloth, pin_group)
        if not pin_stats["nonzero"]:
            raise ValueError(f"Pin vertex group '{pin_group_name}' has no nonzero weights")
        if cloth_modifier.settings.vertex_group_mass != pin_group_name:
            raise ValueError(
                f"Cloth pin group is '{cloth_modifier.settings.vertex_group_mass}', not '{pin_group_name}'; "
                "configure pinning explicitly before creating the attachment"
            )
        target = _get_object(target_object_name)
        if target == cloth:
            raise ValueError("Attachment target must differ from the cloth object")
        if abs(float(cloth.matrix_world.determinant())) <= 1e-12:
            raise ValueError(f"Cloth object '{cloth.name}' has a singular world transform")
        if attachment_type == "ARMATURE" and target.type != "ARMATURE":
            raise ValueError("ARMATURE attachments require an armature target")
        if attachment_type in {"MESH_DEFORM", "SURFACE_DEFORM"} and target.type != "MESH":
            raise ValueError(f"{attachment_type} attachments require a mesh target")
        if bone_name and attachment_type != "HOOK":
            raise ValueError("bone_name is supported only by HOOK attachments")
        if bone_name and target.type != "ARMATURE":
            raise ValueError("A bone-targeted Hook requires an armature target")
        if bone_name and target.data.bones.get(bone_name) is None:
            raise ValueError(f"Bone not found: {bone_name}")
        scene, view_layer = _scene_context_for_object(cloth)
        if target.name not in scene.objects:
            raise ValueError(f"Attachment target '{target.name}' is not linked to cloth scene '{scene.name}'")
        if attachment_type in {"MESH_DEFORM", "SURFACE_DEFORM"}:
            evaluated_target = target.evaluated_get(view_layer.depsgraph)
            target_mesh = evaluated_target.to_mesh()
            try:
                if not target_mesh.vertices or not target_mesh.polygons:
                    raise ValueError(f"Attachment target '{target.name}' must evaluate to a nonempty surface")
            finally:
                evaluated_target.to_mesh_clear()
        _validate_rna_value(scene, "frame_current", rest_frame)

        existing = cloth.modifiers.get(attachment_modifier_name)
        created = False
        if existing is not None:
            if existing.type != attachment_type:
                raise ValueError(f"Modifier '{attachment_modifier_name}' is {existing.type}, not {attachment_type}")
            if existing_policy == "ERROR":
                raise ValueError(f"Attachment modifier already exists: {attachment_modifier_name}")
            modifier = existing
        else:
            modifier = cloth.modifiers.new(name=attachment_modifier_name, type=attachment_type)
            created = True
        original_index = list(cloth.modifiers).index(modifier)
        snapshot = None if created else _snapshot_attachment_modifier(modifier)
        was_bound = bool(getattr(modifier, "is_bound", False))
        if was_bound and attachment_type == "MESH_DEFORM" and modifier.object != target:
            raise ValueError("Cannot retarget an already-bound Mesh Deform modifier")
        if was_bound and attachment_type == "SURFACE_DEFORM" and modifier.target != target:
            raise ValueError("Cannot retarget an already-bound Surface Deform modifier")

        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        ownership = None
        before = None
        try:
            scene.frame_set(rest_frame)
            view_layer.update()
            before = _evaluated_world_vertices(cloth, 10_000, view_layer.depsgraph)
            if attachment_type == "HOOK":
                modifier.object = target
                modifier.subtarget = bone_name or ""
                modifier.vertex_group = pin_group_name
                target_matrix = _attachment_target_matrix(target, bone_name)
                modifier.matrix_inverse = target_matrix.inverted() @ cloth.matrix_world
                modifier.center = cloth.matrix_world.inverted() @ target_matrix.translation
            elif attachment_type == "ARMATURE":
                modifier.object = target
                modifier.vertex_group = pin_group_name
                modifier.use_vertex_groups = True
            elif attachment_type == "MESH_DEFORM":
                modifier.object = target
                modifier.vertex_group = pin_group_name
            else:
                modifier.target = target
                modifier.vertex_group = pin_group_name
            _move_modifier_immediately_before(cloth, modifier, cloth_modifier)
            _tag_update(cloth)
            if attachment_type in {"MESH_DEFORM", "SURFACE_DEFORM"} and bind and not modifier.is_bound:
                _bind_deform_modifier(cloth, modifier)
            if created:
                ownership = _tag_owned_component(cloth, modifier, "attachment")
            _tag_update(cloth)
            after = _evaluated_world_vertices(cloth, 10_000, view_layer.depsgraph)
        except Exception:
            if ownership is not None:
                with contextlib.suppress(Exception):
                    del cloth[ownership["object_property"]]
            if not created and getattr(modifier, "is_bound", False) and not was_bound:
                with contextlib.suppress(Exception):
                    _bind_deform_modifier(cloth, modifier)
            if created:
                with contextlib.suppress(Exception):
                    cloth.modifiers.remove(modifier)
            else:
                _restore_attachment_modifier(modifier, snapshot)
                with contextlib.suppress(Exception):
                    cloth.modifiers.move(list(cloth.modifiers).index(modifier), original_index)
            raise
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()

        rest_displacements = None
        if before["total"] == after["total"] and before["indices"] == after["indices"]:
            displacement_records = []
            for vertex_index, old, new in zip(before["indices"], before["positions"], after["positions"], strict=True):
                try:
                    weight = pin_group.weight(vertex_index)
                except RuntimeError:
                    weight = 0.0
                displacement_records.append((weight, float((new - old).length)))
            displacements = [distance for _weight, distance in displacement_records]
            pinned = [distance for weight, distance in displacement_records if weight > 0]
            unpinned = [distance for weight, distance in displacement_records if weight <= 0]
            rest_displacements = {
                "sampled_vertices": len(displacements),
                "maximum_world": max(displacements, default=0.0),
                "mean_world": statistics.fmean(displacements) if displacements else 0.0,
                "pinned_maximum_world": max(pinned, default=0.0),
                "unpinned_maximum_world": max(unpinned, default=0.0),
                "topology_matched": True,
            }
        return {
            "changed_objects": [cloth.name],
            "cloth_object": cloth.name,
            "cloth_modifier": cloth_modifier.name,
            "attachment": _modifier_info(cloth, modifier),
            "attachment_type": attachment_type,
            "target_object": target.name,
            "target_bone": bone_name,
            "pin_group": pin_stats,
            "created": created,
            "bound": getattr(modifier, "is_bound", None),
            "rest_frame": rest_frame,
            "rest_frame_displacement_check": rest_displacements,
            "ownership": ownership,
            "point_cache": _cache_info(cloth_modifier.point_cache),
            "warnings": [
                "Attachment input changed and invalidates unbaked simulation state.",
                "The rest-frame displacement check is sampled and does not prove behavior at animated frames.",
            ],
        }

    def create_character_cloth_setup(
        self,
        garment_object_name,
        armature_object_name,
        body_collider_object_names,
        pin_group_name,
        collision_collection_name,
        cloth_modifier_name="Cloth",
        armature_modifier_name="Cloth Armature",
        collider_modifier_name="Cloth Collision",
        subdivision_modifier_name="Cloth Subdivision",
        solidify_modifier_name="Cloth Solidify",
        existing_policy="ERROR",
        material=None,
        solver=None,
        collisions=None,
        collider_settings=None,
        add_subdivision=False,
        subdivision_levels=1,
        add_solidify=False,
        solidify_thickness=0.002,
        rest_frame=1,
        cache_frame_start=1,
        cache_frame_end=250,
    ):
        garment = _get_object(garment_object_name, {"MESH"})
        sync_from_editmode(garment)
        armature = _get_object(armature_object_name, {"ARMATURE"})
        if not body_collider_object_names:
            raise ValueError("At least one explicit body collider is required")
        if len(body_collider_object_names) > 64 or len(set(body_collider_object_names)) != len(
            body_collider_object_names
        ):
            raise ValueError("body_collider_object_names must contain 1-64 unique names")
        colliders = [_get_object(name, {"MESH", "CURVE"}) for name in body_collider_object_names]
        if garment in colliders or armature in colliders:
            raise ValueError("Garment, armature, and collider objects must be distinct")
        if existing_policy not in {"ERROR", "REUSE"}:
            raise ValueError("existing_policy must be ERROR or REUSE")
        if cache_frame_start > cache_frame_end:
            raise ValueError("cache_frame_start must be <= cache_frame_end")
        if not cache_frame_start <= rest_frame <= cache_frame_end:
            raise ValueError("rest_frame must be inside the explicit cache frame range")
        if not 0 <= subdivision_levels <= 6:
            raise ValueError("subdivision_levels must be in [0, 6]")
        _finite(solidify_thickness, "solidify_thickness")
        if solidify_thickness <= 0:
            raise ValueError("solidify_thickness must be positive")
        pin_group = garment.vertex_groups.get(pin_group_name)
        if pin_group is None:
            raise ValueError(f"Pin vertex group not found: {pin_group_name}")
        pin_stats = _vertex_group_stats(garment, pin_group)
        if not pin_stats["nonzero"]:
            raise ValueError(f"Pin vertex group '{pin_group_name}' has no nonzero weights")
        collection = bpy.data.collections.get(collision_collection_name)
        if collection is None:
            raise ValueError(f"Collection not found: {collision_collection_name}")
        scene, view_layer = _scene_context_for_object(garment)
        if not _collection_in_scene(collection, scene):
            raise ValueError(f"Collection '{collection.name}' is not linked to scene '{scene.name}'")
        for dependency in [armature, *colliders]:
            if dependency.name not in scene.objects:
                raise ValueError(f"Dependency '{dependency.name}' is not linked to garment scene '{scene.name}'")
        if any(not math.isfinite(value) or value == 0 for value in armature.scale):
            raise ValueError("Armature scale must be finite and nonzero")
        for collider in colliders:
            if collider.type == "MESH":
                sync_from_editmode(collider)
            evaluated = collider.evaluated_get(view_layer.depsgraph)
            evaluated_mesh = evaluated.to_mesh()
            try:
                counts = {
                    "vertices": len(evaluated_mesh.vertices),
                    "faces": len(evaluated_mesh.polygons),
                }
            finally:
                evaluated.to_mesh_clear()
            if not counts["vertices"] or not counts["faces"]:
                raise ValueError(f"Collider '{collider.name}' must evaluate to a nonempty surface")

        collision_patch = dict(collisions or {})
        requested_collection = collision_patch.get("collection_name")
        if requested_collection and requested_collection != collision_collection_name:
            raise ValueError("Collision patch collection_name conflicts with collision_collection_name")
        if collision_patch.get("clear_collection"):
            raise ValueError("Character cloth setup cannot clear its explicit collision collection")
        collision_patch["collection_name"] = collision_collection_name
        collision_patch.setdefault("use_collision", True)
        collider_patch = dict(collider_settings or {})
        if collider_patch.get("use") is False:
            raise ValueError("Character cloth colliders must remain enabled")
        collider_patch["use"] = True

        created_modifiers = []
        ownership_records = []
        created_links = []
        membership_records = []
        existing_modifier_snapshots = []
        cloth_changes = {"material": {}, "solver": {}, "collisions": {}}
        collider_changes = []
        cloth_created = False
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe

        def resolve_modifier(obj, name, modifier_type):
            existing = obj.modifiers.get(name)
            if existing is not None:
                if existing.type != modifier_type:
                    raise ValueError(f"Modifier '{name}' on '{obj.name}' is {existing.type}, not {modifier_type}")
                if existing_policy == "ERROR":
                    raise ValueError(f"Modifier '{name}' already exists on '{obj.name}'")
                return existing, False
            modifier = obj.modifiers.new(name=name, type=modifier_type)
            created_modifiers.append((obj, modifier))
            return modifier, True

        try:
            armature_modifier, armature_created = resolve_modifier(garment, armature_modifier_name, "ARMATURE")
            if not armature_created:
                existing_modifier_snapshots.append(
                    (
                        garment,
                        armature_modifier,
                        list(garment.modifiers).index(armature_modifier),
                        _snapshot_attachment_modifier(armature_modifier),
                    )
                )
            armature_modifier.object = armature
            armature_modifier.use_vertex_groups = True

            cloth_modifier, cloth_created = resolve_modifier(garment, cloth_modifier_name, "CLOTH")
            bpy.context.view_layer.update()
            if cloth_modifier.settings is None or cloth_modifier.collision_settings is None:
                raise RuntimeError("Blender did not initialize Cloth settings")
            if cloth_modifier.point_cache.is_baked:
                raise ValueError("Cannot assemble a character setup around a baked cloth cache")
            old_pin_group = cloth_modifier.settings.vertex_group_mass
            old_cache_range = (cloth_modifier.point_cache.frame_start, cloth_modifier.point_cache.frame_end)
            old_collision_collection = cloth_modifier.collision_settings.collection
            if not cloth_created:
                existing_modifier_snapshots.append(
                    (garment, cloth_modifier, list(garment.modifiers).index(cloth_modifier), None)
                )

            _move_modifier_immediately_before(garment, armature_modifier, cloth_modifier)
            cloth_modifier.settings.vertex_group_mass = pin_group_name
            cloth_changes["material"] = self._configure_material(garment, cloth_modifier, material, None)
            cloth_changes["solver"] = _patch_rna(cloth_modifier.settings, solver or {}, _SOLVER_FIELDS)
            cloth_changes["collisions"] = self._configure_collisions(garment, cloth_modifier, collision_patch)
            for field, value in (
                ("frame_start", cache_frame_start),
                ("frame_end", cache_frame_end),
            ):
                _validate_rna_value(cloth_modifier.point_cache, field, value)
            _set_cache_frame_range(cloth_modifier.point_cache, cache_frame_start, cache_frame_end)

            collider_records = []
            for collider in colliders:
                collision_modifier, collision_created = resolve_modifier(collider, collider_modifier_name, "COLLISION")
                bpy.context.view_layer.update()
                if collider.collision is None:
                    raise RuntimeError(f"Blender did not initialize collision settings for '{collider.name}'")
                if not collision_created:
                    existing_modifier_snapshots.append(
                        (collider, collision_modifier, list(collider.modifiers).index(collision_modifier), None)
                    )
                changes = _patch_rna(collider.collision, collider_patch, _COLLIDER_FIELDS)
                collider_changes.append((collider, changes))
                membership = None
                if collider.name not in collection.objects:
                    collection.objects.link(collider)
                    created_links.append((collection, collider))
                    membership = _tag_owned_membership(collider, collection)
                    membership_records.append((collider, membership))
                if collision_created:
                    ownership = _tag_owned_component(collider, collision_modifier, "collider")
                    ownership_records.append((collider, ownership))
                collider_records.append(
                    {
                        "object": collider.name,
                        "modifier": collision_modifier.name,
                        "modifier_created": collision_created,
                        "collection_membership_created": membership is not None,
                        "settings_changes": changes,
                    }
                )

            subdivision_modifier = None
            if add_subdivision:
                subdivision_modifier, subdivision_created = resolve_modifier(
                    garment, subdivision_modifier_name, "SUBSURF"
                )
                if not subdivision_created:
                    existing_modifier_snapshots.append(
                        (
                            garment,
                            subdivision_modifier,
                            list(garment.modifiers).index(subdivision_modifier),
                            {
                                "levels": subdivision_modifier.levels,
                                "render_levels": subdivision_modifier.render_levels,
                            },
                        )
                    )
                subdivision_modifier.levels = subdivision_levels
                subdivision_modifier.render_levels = subdivision_levels
                _move_modifier_immediately_after(garment, subdivision_modifier, cloth_modifier)

            solidify_modifier = None
            if add_solidify:
                solidify_modifier, solidify_created = resolve_modifier(garment, solidify_modifier_name, "SOLIDIFY")
                if not solidify_created:
                    existing_modifier_snapshots.append(
                        (
                            garment,
                            solidify_modifier,
                            list(garment.modifiers).index(solidify_modifier),
                            {"thickness": solidify_modifier.thickness},
                        )
                    )
                solidify_modifier.thickness = solidify_thickness
                _move_modifier_immediately_after(garment, solidify_modifier, subdivision_modifier or cloth_modifier)

            if cloth_created:
                ownership = _tag_owned_component(garment, cloth_modifier, "cloth")
                ownership_records.append((garment, ownership))
            if armature_created:
                ownership = _tag_owned_component(garment, armature_modifier, "attachment")
                ownership_records.append((garment, ownership))
            for obj, modifier in created_modifiers:
                if obj == garment and modifier in {subdivision_modifier, solidify_modifier}:
                    ownership = _tag_owned_component(obj, modifier, "render_finish")
                    ownership_records.append((obj, ownership))

            scene.frame_set(rest_frame)
            view_layer.update()
            intersection_evidence = [
                {
                    "collider": collider.name,
                    **_evaluated_bvh_overlap(garment, collider, view_layer.depsgraph),
                }
                for collider in colliders
            ]
            _tag_update(garment)
        except Exception:
            for obj, record in reversed(ownership_records + membership_records):
                with contextlib.suppress(Exception):
                    del obj[record["object_property"]]
            for linked_collection, linked_object in reversed(created_links):
                with contextlib.suppress(Exception):
                    linked_collection.objects.unlink(linked_object)
            for collider, changes in reversed(collider_changes):
                _restore_rna(collider.collision, changes)
            if "cloth_modifier" in locals() and not cloth_created:
                _restore_rna(cloth_modifier.settings, cloth_changes["material"])
                _restore_rna(cloth_modifier.settings, cloth_changes["solver"])
                _restore_rna(cloth_modifier.collision_settings, cloth_changes["collisions"])
                cloth_modifier.settings.vertex_group_mass = old_pin_group
                cloth_modifier.collision_settings.collection = old_collision_collection
                _set_cache_frame_range(cloth_modifier.point_cache, *old_cache_range)
            for obj, modifier, original_index, snapshot in reversed(existing_modifier_snapshots):
                if snapshot:
                    if modifier.type in {"HOOK", "ARMATURE", "MESH_DEFORM", "SURFACE_DEFORM"}:
                        _restore_attachment_modifier(modifier, snapshot)
                    else:
                        for name, value in snapshot.items():
                            with contextlib.suppress(Exception):
                                setattr(modifier, name, value)
                with contextlib.suppress(Exception):
                    obj.modifiers.move(list(obj.modifiers).index(modifier), original_index)
            for obj, modifier in reversed(created_modifiers):
                with contextlib.suppress(Exception):
                    obj.modifiers.remove(modifier)
            raise
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()

        recommended_frames = sorted(
            {
                cache_frame_start,
                cache_frame_start + (cache_frame_end - cache_frame_start) // 4,
                cache_frame_start + (cache_frame_end - cache_frame_start) // 2,
                cache_frame_start + 3 * (cache_frame_end - cache_frame_start) // 4,
                cache_frame_end,
            }
        )
        intersection_warnings = [
            item["collider"]
            for item in intersection_evidence
            if item.get("checked") and item.get("overlapping_face_pairs", 0)
        ]
        return {
            "changed_objects": [garment.name, *[collider.name for collider in colliders]],
            "garment": garment.name,
            "armature": armature.name,
            "cloth_modifier": _modifier_info(garment, cloth_modifier),
            "armature_modifier": _modifier_info(garment, armature_modifier),
            "pin_group": pin_stats,
            "collision_collection": collection.name,
            "colliders": collider_records,
            "render_modifiers": {
                "subdivision": _modifier_info(garment, subdivision_modifier) if subdivision_modifier else None,
                "solidify": _modifier_info(garment, solidify_modifier) if solidify_modifier else None,
            },
            "cloth_changes": cloth_changes,
            "point_cache": _cache_info(cloth_modifier.point_cache),
            "rest_frame": rest_frame,
            "rest_frame_intersections": intersection_evidence,
            "armature_scale": list(armature.scale),
            "animation": {"garment": _animation_info(garment), "armature": _animation_info(armature)},
            "dependency_graph": {
                "armature_before_cloth": True,
                "cloth_before_render_finishing": True,
                "live_assets_preserved": True,
            },
            "recommended_test_frames": recommended_frames,
            "ownership": [record for _obj, record in ownership_records + membership_records],
            "warnings": [
                *self._scale_warnings(garment),
                *self._scale_warnings(armature),
                *(
                    [f"Rest-frame evaluated meshes overlap colliders: {intersection_warnings}"]
                    if intersection_warnings
                    else []
                ),
                "Rest-frame overlap is a bounded structural check; review representative animated "
                "frames before production baking.",
            ],
        }

    def sample_cloth_simulation(
        self,
        object_name,
        modifier_name,
        frames,
        vertex_sample_limit=10_000,
        collider_sample_limit=16,
        timeout_seconds=30.0,
    ):
        obj, modifier = _get_cloth(object_name, modifier_name)
        sync_from_editmode(obj)
        if not frames or len(frames) > 100:
            raise ValueError("frames must contain 1-100 explicit frame numbers")
        if any(isinstance(frame, bool) or not isinstance(frame, int) for frame in frames):
            raise ValueError("frames must contain integer frame numbers")
        normalized_frames = sorted(set(frames))
        if len(normalized_frames) != len(frames):
            raise ValueError("frames must not contain duplicates")
        if not 1 <= vertex_sample_limit <= 100_000:
            raise ValueError("vertex_sample_limit must be in [1, 100000]")
        if not 0 <= collider_sample_limit <= 64:
            raise ValueError("collider_sample_limit must be in [0, 64]")
        _finite(timeout_seconds, "timeout_seconds")
        if not 0 < timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be in (0, 300]")
        scene, view_layer = _scene_context_for_object(obj)
        for frame in normalized_frames:
            _validate_rna_value(scene, "frame_current", frame)
        colliders = _eligible_active_colliders(obj, modifier.collision_settings)
        selected_colliders = colliders[:collider_sample_limit]
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        cache_before = _cache_info(modifier.point_cache)
        base_count = len(obj.data.vertices)
        base_indices = _sample_indices(base_count, vertex_sample_limit)
        base_positions = {index: obj.matrix_world @ obj.data.vertices[index].co for index in base_indices}
        base_topology = _topology_summary(obj)
        polygon_limit = min(200_000, max(10_000, vertex_sample_limit * 4))
        collider_face_limit = min(250_000, max(25_000, vertex_sample_limit * 10))
        fps = float(scene.render.fps) / max(float(scene.render.fps_base), 1e-9)
        deadline = time.monotonic() + timeout_seconds
        samples = []
        previous = None
        timed_out = False
        try:
            for frame in normalized_frames:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                scene.frame_set(frame)
                view_layer.update()
                depsgraph = view_layer.depsgraph
                evaluated = obj.evaluated_get(depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    indices = _sample_indices(len(mesh.vertices), vertex_sample_limit)
                    positions = [evaluated.matrix_world @ mesh.vertices[index].co for index in indices]
                    surface = _evaluated_surface_measurements(evaluated, mesh, polygon_limit)
                    displacement = None
                    if len(mesh.vertices) == base_count and indices == base_indices:
                        distances = [
                            float((position - base_positions[index]).length)
                            for index, position in zip(indices, positions, strict=True)
                        ]
                        displacement = {
                            "reference": "BASE_MESH_OBJECT_LOCAL_TRANSFORMED_TO_WORLD",
                            "sample_count": len(distances),
                            "minimum_world": min(distances, default=0.0),
                            "maximum_world": max(distances, default=0.0),
                            "mean_world": statistics.fmean(distances) if distances else 0.0,
                        }
                    velocity = None
                    if previous and previous["indices"] == indices:
                        delta_frames = frame - previous["frame"]
                        speeds = [
                            float((current - prior).length) * fps / delta_frames
                            for prior, current in zip(previous["positions"], positions, strict=True)
                        ]
                        velocity = {
                            "estimate_between_frames": [previous["frame"], frame],
                            "sample_count": len(speeds),
                            "maximum_world_units_per_second": max(speeds, default=0.0),
                            "mean_world_units_per_second": statistics.fmean(speeds) if speeds else 0.0,
                        }
                    inverted = []
                    if len(mesh.polygons) == len(obj.data.polygons):
                        world_normal_matrix = evaluated.matrix_world.to_3x3().inverted_safe().transposed()
                        base_normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
                        for polygon in list(mesh.polygons)[:polygon_limit]:
                            current_normal = world_normal_matrix @ polygon.normal
                            base_normal = base_normal_matrix @ obj.data.polygons[polygon.index].normal
                            if (
                                current_normal.length_squared
                                and base_normal.length_squared
                                and current_normal.normalized().dot(base_normal.normalized()) < 0
                            ):
                                inverted.append(polygon.index)
                    proximity = _collider_proximity(
                        positions,
                        selected_colliders,
                        collider_face_limit,
                        depsgraph,
                    )
                    solver_result = modifier.solver_result
                    sample = {
                        "frame": frame,
                        "evaluated_geometry": {
                            "coordinate_space": "EVALUATED_OBJECT_LOCAL",
                            "vertices": len(mesh.vertices),
                            "edges": len(mesh.edges),
                            "faces": len(mesh.polygons),
                        },
                        "world_bounds": _world_bounds(evaluated),
                        "vertex_sampling": {
                            "sample_count": len(indices),
                            "total_vertices": len(mesh.vertices),
                            "truncated": len(indices) < len(mesh.vertices),
                        },
                        "displacement": displacement,
                        "velocity": velocity,
                        "surface": {
                            **surface,
                            "volume_meaningful": bool(
                                surface["complete"]
                                and len(mesh.vertices) == base_count
                                and base_topology["non_manifold_edges"] == 0
                            ),
                        },
                        "inverted_faces_relative_to_base": {
                            "inverted_count_scanned": len(inverted),
                            "indices_sample": inverted[:100],
                            "sample_truncated": len(inverted) > 100,
                            "available": len(mesh.polygons) == len(obj.data.polygons),
                        },
                        "collider_proximity": proximity,
                        "solver_status": "AVAILABLE" if solver_result is not None else "NOT_INITIALIZED",
                        "solver_result": (
                            _read_fields(
                                solver_result,
                                {
                                    prop.identifier
                                    for prop in solver_result.bl_rna.properties
                                    if prop.identifier != "rna_type"
                                },
                            )
                            if solver_result is not None
                            else None
                        ),
                    }
                    samples.append(sample)
                    previous = {"frame": frame, "indices": indices, "positions": positions}
                finally:
                    evaluated.to_mesh_clear()
                if time.monotonic() >= deadline and frame != normalized_frames[-1]:
                    timed_out = True
                    break
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "requested_frames": normalized_frames,
            "evaluated_frames": [sample["frame"] for sample in samples],
            "timed_out": timed_out,
            "timeout_seconds": timeout_seconds,
            "timeline_restored": {
                "frame": scene.frame_current,
                "subframe": scene.frame_subframe,
            },
            "collider_scope": {
                "eligible": [collider.name for collider in colliders],
                "sampled": [collider.name for collider in selected_colliders],
                "truncated": len(selected_colliders) < len(colliders),
            },
            "samples": samples,
            "point_cache_before": cache_before,
            "point_cache_after": _cache_info(modifier.point_cache),
            "cache_effect": "Evaluation may populate or invalidate Blender's in-memory point cache.",
            "claim": "Bounded measurements only; these samples do not prove stable convergence or visual correctness.",
        }

    def manage_cloth_cache(
        self,
        object_name,
        modifier_name,
        action="INSPECT",
        patch=None,
        confirm_bake=False,
        confirm_free_bake=False,
        confirm_external_overwrite=False,
        max_bake_frames=250,
    ):
        obj, modifier = _get_cloth(object_name, modifier_name)
        cache = modifier.point_cache
        if action not in {"INSPECT", "CONFIGURE", "BAKE", "BAKE_FROM_CACHE", "FREE"}:
            raise ValueError(f"Unsupported cache action: {action}")
        if not 1 <= max_bake_frames <= 10_000:
            raise ValueError("max_bake_frames must be in [1, 10000]")
        patch = dict(patch or {})
        unknown = set(patch) - _POINT_CACHE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported PointCache properties: {sorted(unknown)}")
        if action == "INSPECT" and patch:
            raise ValueError("INSPECT does not accept a configuration patch")
        if action == "CONFIGURE" and not patch:
            raise ValueError("CONFIGURE requires a nonempty PointCache patch")
        if action not in {"INSPECT", "CONFIGURE"} and patch:
            raise ValueError(f"{action} does not accept a configuration patch; configure first")

        before = _cache_info(cache)
        prospective = {
            "frame_start": patch.get("frame_start", cache.frame_start),
            "frame_end": patch.get("frame_end", cache.frame_end),
            "frame_step": patch.get("frame_step", cache.frame_step),
            "name": patch.get("name", cache.name),
            "index": patch.get("index", cache.index),
            "use_disk_cache": patch.get("use_disk_cache", cache.use_disk_cache),
            "use_external": patch.get("use_external", cache.use_external),
            "use_library_path": patch.get("use_library_path", cache.use_library_path),
            "filepath": patch.get("filepath", cache.filepath),
        }
        if prospective["frame_start"] > prospective["frame_end"]:
            raise ValueError("PointCache frame_start must be <= frame_end")
        for name, value in patch.items():
            _validate_rna_value(cache, name, value)
        if prospective["use_external"] and not prospective["filepath"]:
            raise ValueError("External point caches require an explicit filepath")
        if prospective["use_disk_cache"] and not prospective["use_external"] and not bpy.data.filepath:
            raise ValueError("Internal disk caching requires the .blend file to be saved first")
        external = _external_directory_evidence(prospective["filepath"])
        if prospective["use_external"] and (not external["exists"] or not external["writable"]):
            raise ValueError(f"External cache directory must already exist and be writable: {external['resolved']}")
        identity = _prospective_cache_identity(cache, prospective)
        shared_with = []
        dependency_issues = _cloth_cache_dependency_issues(obj, modifier)
        if identity is not None:
            for other_obj, other_modifier, other_cache in _all_cloth_caches():
                if other_modifier == modifier:
                    continue
                if _shared_cache_identity(other_cache) == identity:
                    shared_with.append({"object": other_obj.name, "modifier": other_modifier.name})
        if shared_with and action != "INSPECT":
            raise ValueError(f"External cache identity is already used by {shared_with}")

        if action == "INSPECT":
            return {
                "changed_objects": [],
                "object": obj.name,
                "modifier": modifier.name,
                "action": action,
                "point_cache": before,
                "external_path": external,
                "shared_external_identity_with": shared_with,
                "dependency_issues": dependency_issues,
            }
        if cache.is_baking:
            raise ValueError("Point cache is currently baking")

        changes = {}
        if action == "CONFIGURE":
            if cache.is_baked:
                raise ValueError("Cannot configure a baked point cache; free the exact bake separately first")
            old_range = (cache.frame_start, cache.frame_end)
            scalar_patch = {name: value for name, value in patch.items() if name not in {"frame_start", "frame_end"}}
            try:
                changes = _patch_rna(cache, scalar_patch, _POINT_CACHE_FIELDS)
                if "frame_start" in patch or "frame_end" in patch:
                    _set_cache_frame_range(
                        cache,
                        prospective["frame_start"],
                        prospective["frame_end"],
                    )
                    changes["frame_start"] = {
                        "old": old_range[0],
                        "new": cache.frame_start,
                    }
                    changes["frame_end"] = {
                        "old": old_range[1],
                        "new": cache.frame_end,
                    }
                _tag_update(obj)
            except Exception:
                _restore_rna(cache, changes)
                with contextlib.suppress(Exception):
                    _set_cache_frame_range(cache, *old_range)
                raise
            return {
                "changed_objects": [obj.name],
                "object": obj.name,
                "modifier": modifier.name,
                "action": action,
                "changes": changes,
                "point_cache_before": before,
                "point_cache_after": _cache_info(cache),
                "external_path": _external_directory_evidence(cache.filepath),
                "warnings": ["Point-cache configuration changed; previously evaluated in-memory state is stale."],
            }

        frame_count = (cache.frame_end - cache.frame_start) // cache.frame_step + 1
        if action in {"BAKE", "BAKE_FROM_CACHE"}:
            if not confirm_bake:
                raise ValueError(f"{action} requires confirm_bake=True")
            if cache.is_baked:
                raise ValueError("Point cache is already baked")
            if dependency_issues:
                raise ValueError(f"Cloth cache dependencies are invalid: {dependency_issues}")
            if frame_count > max_bake_frames:
                raise ValueError(
                    f"Cache range contains {frame_count} steps, exceeding max_bake_frames={max_bake_frames}"
                )
            if action == "BAKE" and cache.use_external and external["entries"] and not confirm_external_overwrite:
                raise ValueError("External cache directory is not empty; confirm_external_overwrite=True is required")
            if action == "BAKE":
                _run_point_cache_operator(obj, cache, bpy.ops.ptcache.bake, bake=True)
            else:
                _run_point_cache_operator(obj, cache, bpy.ops.ptcache.bake_from_cache)
            if not cache.is_baked:
                raise RuntimeError(
                    f"{action} reported FINISHED but the exact point cache is not baked; "
                    f"state={json.dumps(_cache_info(cache))}"
                )
        else:
            if not confirm_free_bake:
                raise ValueError("FREE requires confirm_free_bake=True")
            if not cache.is_baked:
                raise ValueError("Point cache is not baked")
            if cache.use_external and external["entries"] and not confirm_external_overwrite:
                raise ValueError(
                    "Freeing an external bake may remove cache files; confirm_external_overwrite=True is also required"
                )
            _run_point_cache_operator(obj, cache, bpy.ops.ptcache.free_bake)
            if cache.is_baked:
                raise RuntimeError(
                    "FREE reported FINISHED but the exact point cache remains baked; "
                    f"state={json.dumps(_cache_info(cache))}"
                )
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "action": action,
            "frame_steps": frame_count,
            "operator_scope": "EXACT_CLOTH_POINT_CACHE",
            "point_cache_before": before,
            "point_cache_after": _cache_info(cache),
            "external_path": _external_directory_evidence(cache.filepath),
            "warnings": [
                "Bake operators run synchronously; this tool bounds frames but cannot interrupt Blender "
                "inside one frame solve."
            ],
        }

    def remove_cloth_components(
        self,
        object_name,
        component_type,
        modifier_name=None,
        collection_name=None,
        confirm_baked_removal=False,
        confirm_affected_bakes=False,
    ):
        obj = _get_object(object_name)
        allowed = {
            "CLOTH_MODIFIER",
            "COLLISION_MODIFIER",
            "ATTACHMENT_MODIFIER",
            "COLLISION_COLLECTION_MEMBERSHIP",
        }
        if component_type not in allowed:
            raise ValueError(f"Unsupported component_type: {component_type}")
        if component_type == "COLLISION_COLLECTION_MEMBERSHIP":
            if modifier_name is not None:
                raise ValueError("modifier_name is not valid for collection membership removal")
            if not collection_name:
                raise ValueError("collection_name is required for collection membership removal")
            collection = bpy.data.collections.get(collection_name)
            if collection is None:
                raise ValueError(f"Collection not found: {collection_name}")
            if obj.name not in collection.objects:
                raise ValueError(f"Object '{obj.name}' is not directly linked to collection '{collection.name}'")
            ownership = _owned_membership_record(obj, collection.name)
            if ownership is None:
                raise ValueError("Collection membership is not marked as MCP-owned and will not be removed")
            affected = _affected_cloths(obj)
            baked = [
                {"object": cloth.name, "modifier": modifier.name}
                for cloth, modifier in affected
                if modifier.point_cache.is_baked
            ]
            if baked and not confirm_affected_bakes:
                raise ValueError(f"Collection membership affects baked cloth caches {baked}")
            serialized_ownership = obj[ownership["object_property"]]
            try:
                collection.objects.unlink(obj)
                del obj[ownership["object_property"]]
            except Exception:
                with contextlib.suppress(Exception):
                    if obj.name not in collection.objects:
                        collection.objects.link(obj)
                    obj[ownership["object_property"]] = serialized_ownership
                raise
            return {
                "changed_objects": [obj.name],
                "object": obj.name,
                "component_type": component_type,
                "removed": {"collection_membership": collection.name, "ownership": ownership},
                "affected_cloth_caches": [
                    {
                        "object": cloth.name,
                        "modifier": modifier.name,
                        "point_cache": _cache_info(modifier.point_cache),
                    }
                    for cloth, modifier in affected
                ],
                "retained": ["object", "other collection memberships", "modifiers", "vertex groups"],
            }

        if collection_name is not None:
            raise ValueError("collection_name is valid only for collection membership removal")
        if not modifier_name:
            raise ValueError("modifier_name is required for modifier removal")
        expected_type = {
            "CLOTH_MODIFIER": "CLOTH",
            "COLLISION_MODIFIER": "COLLISION",
        }.get(component_type)
        modifier = obj.modifiers.get(modifier_name)
        if modifier is None:
            raise ValueError(f"Modifier not found: {modifier_name}")
        if expected_type and modifier.type != expected_type:
            raise ValueError(f"Modifier '{modifier.name}' is {modifier.type}, not {expected_type}")
        ownership_role = {
            "CLOTH_MODIFIER": "cloth",
            "COLLISION_MODIFIER": "collider",
            "ATTACHMENT_MODIFIER": "attachment",
        }[component_type]
        ownership = next(
            (
                record
                for record in _owned_component_records(obj)
                if record.get("role") == ownership_role and record.get("modifier") == modifier.name
            ),
            None,
        )
        if component_type == "ATTACHMENT_MODIFIER":
            if modifier.type not in {"HOOK", "ARMATURE", "MESH_DEFORM", "SURFACE_DEFORM"}:
                raise ValueError(f"Modifier '{modifier.name}' is not a supported attachment type")
            if ownership is None:
                raise ValueError("Attachment modifier is not marked as MCP-owned and will not be removed")

        if component_type == "CLOTH_MODIFIER":
            affected = [(obj, modifier)]
            if modifier.point_cache.is_baked and not confirm_baked_removal:
                raise ValueError("Removing a baked Cloth modifier requires confirm_baked_removal=True")
        elif component_type == "COLLISION_MODIFIER":
            affected = _affected_cloths(obj)
        else:
            affected = [(obj, item) for item in obj.modifiers if item.type == "CLOTH"]
        baked_affected = [
            {"object": cloth.name, "modifier": cloth_modifier.name}
            for cloth, cloth_modifier in affected
            if cloth_modifier.point_cache.is_baked
            and not (component_type == "CLOTH_MODIFIER" and cloth_modifier == modifier)
        ]
        if baked_affected and not confirm_affected_bakes:
            raise ValueError(f"Removal affects baked cloth caches {baked_affected}")

        group_names = []
        cache_evidence = None
        if modifier.type == "CLOTH":
            group_names = [
                value
                for value in [
                    *[
                        getattr(modifier.settings, name, "")
                        for name in (
                            "vertex_group_mass",
                            "vertex_group_structural_stiffness",
                            "vertex_group_shear_stiffness",
                            "vertex_group_bending",
                            "vertex_group_shrink",
                            "vertex_group_pressure",
                            "vertex_group_intern",
                        )
                    ],
                    modifier.collision_settings.vertex_group_object_collisions,
                    modifier.collision_settings.vertex_group_self_collisions,
                ]
                if value
            ]
            cache_evidence = _cache_info(modifier.point_cache)
        elif hasattr(modifier, "vertex_group") and modifier.vertex_group:
            group_names = [modifier.vertex_group]
        drivers = _modifier_driver_paths(obj, modifier)
        downstream = [item.name for item in list(obj.modifiers)[list(obj.modifiers).index(modifier) + 1 :]]
        affected_cache_evidence = [
            {
                "object": cloth.name,
                "modifier": cloth_modifier.name,
                "point_cache": _cache_info(cloth_modifier.point_cache),
            }
            for cloth, cloth_modifier in affected
            if not (cloth == obj and cloth_modifier == modifier)
        ]
        ownership_value = obj.get(ownership["object_property"]) if ownership else None
        if ownership:
            del obj[ownership["object_property"]]
        try:
            obj.modifiers.remove(modifier)
        except Exception:
            if ownership and ownership_value is not None:
                obj[ownership["object_property"]] = ownership_value
            raise
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "component_type": component_type,
            "removed": {
                "modifier": modifier_name,
                "modifier_type": expected_type or "ATTACHMENT",
                "ownership": ownership,
            },
            "preflight_dependencies": {
                "referenced_vertex_groups_retained": sorted(set(group_names)),
                "drivers_now_unresolved": drivers,
                "downstream_modifiers_retained": downstream,
                "point_cache_removed_with_modifier": cache_evidence,
                "external_cache_files_deleted": False,
            },
            "affected_cloth_caches": affected_cache_evidence,
            "retained": [
                "source object and mesh",
                "materials",
                "vertex groups",
                "control objects",
                "other modifiers",
                "external cache directories and files",
            ],
        }

    def create_cloth_proxy_rig(
        self,
        render_object_name,
        proxy_object_name,
        proxy_source_policy="EXISTING",
        bind_type="SURFACE_DEFORM",
        cloth_modifier_name="Cloth",
        bind_modifier_name="Cloth Proxy Bind",
        existing_policy="ERROR",
        allow_topology_change=False,
        decimate_ratio=0.25,
        vertex_group_name=None,
        surface_deform_falloff=4.0,
        mesh_deform_precision=5,
        rest_frame=1,
        validation_frames=None,
    ):
        render_obj = _get_object(render_object_name, {"MESH"})
        sync_from_editmode(render_obj)
        _validate_id_name(proxy_object_name, "proxy_object_name")
        _validate_id_name(cloth_modifier_name, "cloth_modifier_name")
        _validate_id_name(bind_modifier_name, "bind_modifier_name")
        if proxy_source_policy not in {"EXISTING", "DUPLICATE_RENDER", "DECIMATE_RENDER"}:
            raise ValueError("proxy_source_policy must be EXISTING, DUPLICATE_RENDER, or DECIMATE_RENDER")
        if bind_type not in {"SURFACE_DEFORM", "MESH_DEFORM"}:
            raise ValueError("bind_type must be SURFACE_DEFORM or MESH_DEFORM")
        if existing_policy not in {"ERROR", "REUSE"}:
            raise ValueError("existing_policy must be ERROR or REUSE")
        if render_object_name == proxy_object_name:
            raise ValueError("Render and proxy objects must be distinct")
        _finite(decimate_ratio, "decimate_ratio")
        if not 0.01 <= decimate_ratio <= 1.0:
            raise ValueError("decimate_ratio must be in [0.01, 1.0]")
        if proxy_source_policy == "DECIMATE_RENDER" and not allow_topology_change:
            raise ValueError("DECIMATE_RENDER requires allow_topology_change=True")
        frame_set: set[int] = {int(rest_frame)}
        frame_set.update(int(frame) for frame in validation_frames or [])
        frames = sorted(frame_set)
        if len(frames) > 12:
            raise ValueError("At most 12 unique rest/validation frames may be evaluated")
        if vertex_group_name and render_obj.vertex_groups.get(vertex_group_name) is None:
            raise ValueError(f"Render vertex group not found: {vertex_group_name}")
        scene, view_layer = _scene_context_for_object(render_obj)
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        created_proxy = False
        proxy_data = None
        created_cloth = False
        created_bind = False
        bound_during_request = False
        ownership_records = []
        original_bind_index = None
        bind_snapshot = None
        proxy_obj = None
        simulation_id = uuid.uuid4().hex
        rig_property = f"{_OWNERSHIP_PREFIX}_proxy_rig_{simulation_id}"
        try:
            existing_proxy = bpy.data.objects.get(proxy_object_name)
            if proxy_source_policy == "EXISTING":
                proxy_obj = _get_object(proxy_object_name, {"MESH"})
                sync_from_editmode(proxy_obj)
                if proxy_obj.name not in scene.objects:
                    raise ValueError(f"Proxy '{proxy_obj.name}' is not linked to render scene '{scene.name}'")
            else:
                if existing_proxy is not None:
                    raise ValueError(f"Object already exists: {proxy_object_name}")
                proxy_obj = render_obj.copy()
                proxy_obj.name = proxy_object_name
                proxy_data = render_obj.data.copy()
                proxy_obj.data = proxy_data
                for modifier in list(proxy_obj.modifiers):
                    if modifier.type not in {"ARMATURE", "HOOK", "LATTICE"}:
                        proxy_obj.modifiers.remove(modifier)
                collection = render_obj.users_collection[0] if render_obj.users_collection else scene.collection
                collection.objects.link(proxy_obj)
                created_proxy = True
                ownership_records.append(
                    (proxy_obj, _tag_owned_object(proxy_obj, "simulation_proxy", simulation_id, render_obj.name))
                )
                if proxy_source_policy == "DECIMATE_RENDER" and decimate_ratio < 1.0:
                    if getattr(proxy_obj.data, "shape_keys", None) is not None:
                        proxy_obj.shape_key_clear()
                    decimate = proxy_obj.modifiers.new(name="Cloth Proxy Decimate", type="DECIMATE")
                    decimate.decimate_type = "COLLAPSE"
                    decimate.ratio = decimate_ratio
                    proxy_obj.modifiers.move(list(proxy_obj.modifiers).index(decimate), 0)
                    _apply_named_modifier(proxy_obj, decimate)
            if not proxy_obj.data.vertices or not proxy_obj.data.polygons:
                raise ValueError(f"Proxy '{proxy_obj.name}' must have nonempty vertices and faces")

            cloth_modifier = proxy_obj.modifiers.get(cloth_modifier_name)
            if cloth_modifier is not None:
                if cloth_modifier.type != "CLOTH":
                    raise ValueError(f"Modifier '{cloth_modifier_name}' on proxy is not Cloth")
                if existing_policy == "ERROR":
                    raise ValueError(f"Cloth modifier '{cloth_modifier_name}' already exists on proxy")
                _reject_baked([(proxy_obj, cloth_modifier)])
            else:
                cloth_modifier = proxy_obj.modifiers.new(name=cloth_modifier_name, type="CLOTH")
                created_cloth = True
                view_layer.update()
                _configure_independent_cache(
                    cloth_modifier.point_cache,
                    proxy_obj.name,
                    cloth_modifier.name,
                    identity_token=simulation_id,
                )
                ownership_records.append(
                    (
                        proxy_obj,
                        _tag_owned_component(
                            proxy_obj,
                            cloth_modifier,
                            "cloth_proxy",
                            simulation_id,
                            render_obj.name,
                        ),
                    )
                )
            if cloth_modifier.settings is None or cloth_modifier.point_cache is None:
                raise RuntimeError("Blender did not initialize the proxy Cloth modifier")

            bind_modifier = render_obj.modifiers.get(bind_modifier_name)
            if bind_modifier is not None:
                if bind_modifier.type != bind_type:
                    raise ValueError(
                        f"Modifier '{bind_modifier_name}' is {bind_modifier.type}, not requested {bind_type}"
                    )
                if existing_policy == "ERROR":
                    raise ValueError(f"Binding modifier '{bind_modifier_name}' already exists")
                original_bind_index = list(render_obj.modifiers).index(bind_modifier)
                bind_snapshot = _snapshot_attachment_modifier(bind_modifier)
                current_target = _modifier_dependency_target(bind_modifier)
                if bind_modifier.is_bound and current_target != proxy_obj:
                    raise ValueError("A bound reused deformation modifier cannot be retargeted safely")
            else:
                bind_modifier = render_obj.modifiers.new(name=bind_modifier_name, type=bind_type)
                created_bind = True
            if bind_modifier.is_bound:
                expected_setting = (
                    float(bind_modifier.falloff) == float(surface_deform_falloff)
                    if bind_type == "SURFACE_DEFORM"
                    else int(bind_modifier.precision) == int(mesh_deform_precision)
                )
                if bind_modifier.vertex_group != (vertex_group_name or "") or not expected_setting:
                    raise ValueError("A bound reused deformation modifier does not match the requested settings")
            else:
                _set_modifier_dependency_target(bind_modifier, proxy_obj)
                if hasattr(bind_modifier, "vertex_group"):
                    bind_modifier.vertex_group = vertex_group_name or ""
                if bind_type == "SURFACE_DEFORM":
                    _validate_rna_value(bind_modifier, "falloff", surface_deform_falloff)
                    bind_modifier.falloff = surface_deform_falloff
                else:
                    _validate_rna_value(bind_modifier, "precision", mesh_deform_precision)
                    bind_modifier.precision = mesh_deform_precision
                scene.frame_set(rest_frame)
                view_layer.update()
                proximity = _proxy_proximity_evidence(render_obj, proxy_obj, view_layer.depsgraph)
                _bind_deform_modifier(render_obj, bind_modifier)
                bound_during_request = True
            if bind_modifier.is_bound and "proximity" not in locals():
                proximity = _proxy_proximity_evidence(render_obj, proxy_obj, view_layer.depsgraph)
            if created_bind:
                ownership_records.append(
                    (
                        render_obj,
                        _tag_owned_component(
                            render_obj,
                            bind_modifier,
                            "proxy_binding",
                            simulation_id,
                            proxy_obj.name,
                        ),
                    )
                )
            validation = []
            for frame in frames:
                scene.frame_set(frame)
                view_layer.update()
                validation.append(
                    {
                        "frame": frame,
                        "proxy": _evaluated_geometry_evidence(proxy_obj, view_layer.depsgraph),
                        "render": _evaluated_geometry_evidence(render_obj, view_layer.depsgraph),
                    }
                )
            render_obj[rig_property] = json.dumps(
                {
                    "owned": True,
                    "simulation_id": simulation_id,
                    "role": "proxy_rig",
                    "proxy": proxy_obj.name,
                    "render": render_obj.name,
                    "binding": bind_modifier.name,
                    "schema_version": _MCP_SCHEMA_VERSION,
                },
                sort_keys=True,
            )
            _tag_update(render_obj)
        except Exception:
            with contextlib.suppress(Exception):
                _remove_custom_property(render_obj, rig_property)
            for owner, record in reversed(ownership_records):
                with contextlib.suppress(Exception):
                    del owner[record["object_property"]]
            if created_bind and "bind_modifier" in locals():
                with contextlib.suppress(Exception):
                    render_obj.modifiers.remove(bind_modifier)
            elif bind_snapshot is not None and "bind_modifier" in locals():
                if bound_during_request:
                    with contextlib.suppress(Exception):
                        _unbind_deform_modifier(render_obj, bind_modifier)
                _restore_attachment_modifier(bind_modifier, bind_snapshot)
                with contextlib.suppress(Exception):
                    render_obj.modifiers.move(list(render_obj.modifiers).index(bind_modifier), original_bind_index)
            if created_cloth and proxy_obj is not None and not created_proxy:
                with contextlib.suppress(Exception):
                    proxy_obj.modifiers.remove(cloth_modifier)
            if created_proxy and proxy_obj is not None:
                _remove_created_object(proxy_obj, proxy_data)
            raise
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()
        warnings = []
        topology = _topology_summary(proxy_obj)
        if topology["non_manifold_edges"]:
            warnings.append("Proxy mesh is non-manifold; Mesh Deform binding and cloth behavior may be unreliable.")
        if proximity["maximum_distance"] and proximity["maximum_distance"] > max(render_obj.dimensions) * 0.1:
            warnings.append("Some render vertices are far from the proxy relative to the render bounds.")
        return {
            "changed_objects": [render_obj.name, proxy_obj.name],
            "changed_resources": [proxy_data.name] if proxy_data is not None else [],
            "render_object": render_obj.name,
            "proxy_object": proxy_obj.name,
            "proxy_created": created_proxy,
            "simulation_id": simulation_id,
            "proxy_source_policy": proxy_source_policy,
            "topology_changed": proxy_source_policy == "DECIMATE_RENDER" and decimate_ratio < 1.0,
            "cloth_modifier": _modifier_info(proxy_obj, cloth_modifier),
            "binding_modifier": {**_modifier_info(render_obj, bind_modifier), "is_bound": bind_modifier.is_bound},
            "rest_frame": rest_frame,
            "rest_coverage": proximity,
            "proxy_topology": topology,
            "validation_frames": validation,
            "source_geometry_preserved": True,
            "retained_live_dependencies": True,
            "ownership": [record for _owner, record in ownership_records],
            "warnings": warnings,
        }

    def duplicate_cloth_setup_variant(
        self,
        source_object_name,
        variant_object_name,
        variant_collection_name,
        name_suffix,
        mesh_data_policy,
        material_policy,
        animation_policy,
        collider_policy,
        force_field_policy,
        render_surface_policy,
        cache_directory=None,
    ):
        source = _get_object(source_object_name, {"MESH"})
        sync_from_editmode(source)
        _validate_id_name(variant_object_name, "variant_object_name")
        _validate_id_name(variant_collection_name, "variant_collection_name")
        cloth_modifiers = [modifier for modifier in source.modifiers if modifier.type == "CLOTH"]
        if not cloth_modifiers:
            raise ValueError(f"Object '{source.name}' has no Cloth modifier")
        if bpy.data.objects.get(variant_object_name) is not None:
            raise ValueError(f"Object already exists: {variant_object_name}")
        if bpy.data.collections.get(variant_collection_name) is not None:
            raise ValueError(f"Collection already exists: {variant_collection_name}")
        if not name_suffix or len(name_suffix) > 32:
            raise ValueError("name_suffix must contain 1-32 characters")
        if mesh_data_policy not in {"COPY", "SHARE"} or material_policy not in {"COPY", "SHARE"}:
            raise ValueError("Mesh and material policies must be COPY or SHARE")
        if animation_policy not in {"COPY", "SHARE"}:
            raise ValueError("animation_policy must be COPY or SHARE")
        if collider_policy not in {"DUPLICATE", "SHARE"} or force_field_policy not in {"DUPLICATE", "SHARE"}:
            raise ValueError("Collider and force-field policies must be DUPLICATE or SHARE")
        if render_surface_policy not in {"DUPLICATE", "OMIT"}:
            raise ValueError("render_surface_policy must be DUPLICATE or OMIT")
        if material_policy == "COPY" and mesh_data_policy != "COPY":
            raise ValueError("material_policy=COPY requires mesh_data_policy=COPY")
        if cache_directory:
            resolved = bpy.path.abspath(cache_directory)
            if not os.path.isdir(resolved) or not os.access(resolved, os.W_OK):
                raise ValueError(f"cache_directory must be an existing writable directory: {cache_directory}")
        scene, view_layer = _scene_context_for_object(source)

        colliders = {}
        effectors = {}
        for modifier in cloth_modifiers:
            collision_collection = modifier.collision_settings.collection
            if collision_collection:
                for obj in collision_collection.all_objects:
                    if any(item.type == "COLLISION" for item in obj.modifiers):
                        colliders[obj.name] = obj
            effector_collection = modifier.settings.effector_weights.collection
            if effector_collection:
                for obj in effector_collection.all_objects:
                    if getattr(getattr(obj, "field", None), "type", "NONE") != "NONE":
                        effectors[obj.name] = obj
        render_surfaces = {}
        for candidate in scene.objects:
            if candidate == source:
                continue
            for modifier in candidate.modifiers:
                if (
                    modifier.type in {"SURFACE_DEFORM", "MESH_DEFORM"}
                    and _modifier_dependency_target(modifier) == source
                ):
                    render_surfaces[candidate.name] = candidate
                    break
        duplicate_dependencies = []
        if collider_policy == "DUPLICATE":
            duplicate_dependencies.extend(colliders.values())
        if force_field_policy == "DUPLICATE":
            duplicate_dependencies.extend(effectors.values())
        if render_surface_policy == "DUPLICATE":
            duplicate_dependencies.extend(render_surfaces.values())
        duplicate_dependencies = list(dict.fromkeys(duplicate_dependencies))
        generated_names = [f"{obj.name}{name_suffix}" for obj in duplicate_dependencies]
        for generated_name in generated_names:
            _validate_id_name(generated_name, "generated dependency name")
        if len({variant_object_name, *generated_names}) != len(generated_names) + 1:
            raise ValueError("Variant and generated dependency object names must be unique")
        for label, enabled in (
            ("Colliders", collider_policy == "DUPLICATE" and bool(colliders)),
            ("Effectors", force_field_policy == "DUPLICATE" and bool(effectors)),
            ("Render Surfaces", render_surface_policy == "DUPLICATE" and bool(render_surfaces)),
        ):
            if enabled:
                child_name = _validate_id_name(f"{variant_collection_name} {label}", "variant child collection")
                if bpy.data.collections.get(child_name) is not None:
                    raise ValueError(f"Collection already exists: {child_name}")
        collisions = [name for name in generated_names if bpy.data.objects.get(name) is not None]
        if collisions:
            raise ValueError(f"Variant dependency object names already exist: {collisions}")

        root_collection = bpy.data.collections.new(variant_collection_name)
        scene.collection.children.link(root_collection)
        created_collections = [root_collection]
        created = []
        copied_materials = []
        copied_actions = []
        ownership = []
        source_map = {}
        simulation_id = uuid.uuid4().hex
        try:
            variant, data, materials, actions = _duplicate_object(
                source,
                variant_object_name,
                root_collection,
                copy_mesh=mesh_data_policy == "COPY",
                material_policy=material_policy,
                animation_policy=animation_policy,
            )
            created.append((variant, data, materials, actions))
            copied_materials.extend(materials)
            copied_actions.extend(actions)
            source_map[source.name] = variant.name
            for key in list(variant.keys()):
                if key.startswith(_OWNERSHIP_PREFIX):
                    del variant[key]
            variant_cloth = [modifier for modifier in variant.modifiers if modifier.type == "CLOTH"]
            for cache_index, modifier in enumerate(variant_cloth):
                if modifier.point_cache.is_baked or modifier.point_cache.is_baking:
                    raise ValueError("Copied Cloth modifier unexpectedly retained an active bake state")
                _configure_independent_cache(
                    modifier.point_cache,
                    variant.name,
                    modifier.name,
                    cache_directory,
                    cache_index,
                    simulation_id,
                )
                source_modifier = source.modifiers.get(modifier.name)
                if source_modifier is not None and source_modifier.type == "CLOTH":
                    if modifier.point_cache is source_modifier.point_cache:
                        raise RuntimeError("Variant Cloth modifier shares the source PointCache instance")
                    variant_identity = _shared_cache_identity(modifier.point_cache)
                    if variant_identity is not None and variant_identity == _shared_cache_identity(
                        source_modifier.point_cache
                    ):
                        raise RuntimeError("Variant Cloth modifier retained the source external cache identity")
                ownership.append(
                    (variant, _tag_owned_component(variant, modifier, "cloth_variant", simulation_id, source.name))
                )

            duplicate_map = {source.name: variant}

            def duplicate_group(objects, label):
                if not objects:
                    return None
                collection = bpy.data.collections.new(f"{variant_collection_name} {label}")
                root_collection.children.link(collection)
                created_collections.append(collection)
                for original in sorted(objects.values(), key=lambda item: item.name):
                    if original.name in duplicate_map:
                        duplicate = duplicate_map[original.name]
                        if duplicate.name not in collection.objects:
                            collection.objects.link(duplicate)
                        continue
                    duplicate, copied_data, materials, actions = _duplicate_object(
                        original,
                        f"{original.name}{name_suffix}",
                        collection,
                        copy_mesh=bool(original.data),
                        material_policy=material_policy if original.type == "MESH" else "SHARE",
                        animation_policy=animation_policy,
                    )
                    created.append((duplicate, copied_data, materials, actions))
                    copied_materials.extend(materials)
                    copied_actions.extend(actions)
                    for key in list(duplicate.keys()):
                        if key.startswith(_OWNERSHIP_PREFIX):
                            del duplicate[key]
                    duplicate_map[original.name] = duplicate
                    source_map[original.name] = duplicate.name
                return collection

            collider_collection = duplicate_group(colliders, "Colliders") if collider_policy == "DUPLICATE" else None
            effector_collection = duplicate_group(effectors, "Effectors") if force_field_policy == "DUPLICATE" else None
            duplicate_group(render_surfaces, "Render Surfaces") if render_surface_policy == "DUPLICATE" else None

            for modifier in variant_cloth:
                if collider_collection and modifier.collision_settings.collection is not None:
                    modifier.collision_settings.collection = collider_collection
                if effector_collection and modifier.settings.effector_weights.collection is not None:
                    modifier.settings.effector_weights.collection = effector_collection
            for original_name, duplicate in duplicate_map.items():
                original = bpy.data.objects.get(original_name)
                if original is None:
                    raise RuntimeError(f"Variant source object disappeared during duplication: {original_name}")
                for modifier in duplicate.modifiers:
                    target = _modifier_dependency_target(modifier)
                    target_name = target.name if target is not None else None
                    if target_name not in duplicate_map:
                        continue
                    if modifier.type in {"SURFACE_DEFORM", "MESH_DEFORM"} and modifier.is_bound:
                        _unbind_deform_modifier(duplicate, modifier)
                    _set_modifier_dependency_target(modifier, duplicate_map[target_name])
                    if modifier.type in {"SURFACE_DEFORM", "MESH_DEFORM"}:
                        _bind_deform_modifier(duplicate, modifier)
                if original != source:
                    role = (
                        "variant_render_surface"
                        if original in render_surfaces.values()
                        else "variant_collider"
                        if original in colliders.values()
                        else "variant_effector"
                    )
                    ownership.append(
                        (
                            duplicate,
                            _tag_owned_object(duplicate, role, simulation_id, original.name),
                        )
                    )
            view_layer.update()
        except Exception:
            for owner, record in reversed(ownership):
                with contextlib.suppress(Exception):
                    del owner[record["object_property"]]
            for obj, data, materials, actions in reversed(created):
                _remove_created_object(obj, data, materials, actions)
            for collection in reversed(created_collections):
                with contextlib.suppress(Exception):
                    bpy.data.collections.remove(collection)
            raise
        caches = [
            {"modifier": modifier.name, "point_cache": _cache_info(modifier.point_cache)}
            for modifier in variant.modifiers
            if modifier.type == "CLOTH"
        ]
        return {
            "changed_objects": sorted(source_map.values()),
            "changed_resources": list(
                dict.fromkeys(
                    [
                        *[collection.name for collection in created_collections],
                        *[data.name for _obj, data, _materials, _actions in created if data is not None],
                        *[material.name for material in copied_materials],
                        *[action.name for action in copied_actions],
                    ]
                )
            ),
            "source_object": source.name,
            "variant_object": variant.name,
            "variant_collection": root_collection.name,
            "simulation_id": simulation_id,
            "source_to_variant": source_map,
            "policies": {
                "mesh_data": mesh_data_policy,
                "materials": material_policy,
                "animation": animation_policy,
                "colliders": collider_policy,
                "force_fields": force_field_policy,
                "render_surfaces": render_surface_policy,
            },
            "dependencies": {
                "colliders": sorted(colliders),
                "force_fields": sorted(effectors),
                "render_surfaces": sorted(render_surfaces),
                "unremapped_attachment_targets": sorted(
                    {
                        target.name
                        for modifier in variant.modifiers
                        if modifier.type in {"HOOK", "ARMATURE", "MESH_DEFORM", "SURFACE_DEFORM"}
                        if (target := _modifier_dependency_target(modifier)) is not None
                        and target not in duplicate_map.values()
                    }
                ),
            },
            "point_caches": caches,
            "mesh_data_shared": variant.data == source.data,
            "shape_keys_shared": getattr(variant.data, "shape_keys", None) == getattr(source.data, "shape_keys", None),
            "ownership": [record for _owner, record in ownership],
            "warnings": ["Shared dependencies remain intentionally coupled to the source setup."]
            if "SHARE" in {collider_policy, force_field_policy, animation_policy, mesh_data_policy}
            else [],
        }

    def prepare_cloth_render_surface(
        self,
        object_name,
        cloth_modifier_name,
        corrective_smooth=None,
        subdivision=None,
        solidify=None,
        weighted_normal=None,
        corrective_smooth_name="Cloth Corrective Smooth",
        subdivision_name="Cloth Render Subdivision",
        solidify_name="Cloth Render Thickness",
        weighted_normal_name="Cloth Weighted Normal",
        existing_policy="ERROR",
        rest_frame=1,
    ):
        obj, cloth_modifier = _get_cloth(object_name, cloth_modifier_name)
        sync_from_editmode(obj)
        scene, view_layer = _scene_context_for_object(obj)
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        if existing_policy not in {"ERROR", "REUSE"}:
            raise ValueError("existing_policy must be ERROR or REUSE")
        requests = [
            ("CORRECTIVE_SMOOTH", corrective_smooth_name, corrective_smooth, _CORRECTIVE_SMOOTH_FIELDS),
            ("SUBSURF", subdivision_name, subdivision, _SUBDIVISION_FIELDS),
            ("SOLIDIFY", solidify_name, solidify, _SOLIDIFY_FIELDS),
            ("WEIGHTED_NORMAL", weighted_normal_name, weighted_normal, _WEIGHTED_NORMAL_FIELDS),
        ]
        requested = [item for item in requests if item[2] is not None]
        if not requested:
            raise ValueError("At least one render-finishing patch is required")
        if len({name for _kind, name, _patch, _fields in requested}) != len(requested):
            raise ValueError("Requested render modifier names must be unique")
        for _kind, _name, patch, _fields in requested:
            group_name = patch.get("vertex_group") if patch else None
            if group_name and obj.vertex_groups.get(group_name) is None:
                raise ValueError(f"Vertex group not found: {group_name}")
        if corrective_smooth and corrective_smooth.get("iterations", 0) > 200:
            raise ValueError("Corrective Smooth iterations are limited to 200")
        if subdivision:
            for field in ("levels", "render_levels"):
                if subdivision.get(field, 0) > 6:
                    raise ValueError(f"{field} is limited to 6 for bounded evaluated geometry")
        if solidify:
            if solidify.get("thickness") == 0:
                raise ValueError("Solidify thickness must be nonzero")
            material_count = len(obj.material_slots)
            for field in ("material_offset", "material_offset_rim"):
                offset = int(solidify.get(field, 0) or 0)
                if offset and not material_count:
                    raise ValueError(f"{field} requires at least one material slot")
                if offset and any(
                    not 0 <= polygon.material_index + offset < material_count for polygon in obj.data.polygons
                ):
                    raise ValueError(f"{field} would resolve outside the object's material slots")
        before = _evaluated_geometry_evidence(obj)
        created = []
        reused = []
        ownership = []
        snapshots = []
        bound_during_request = []
        try:
            preceding = cloth_modifier
            records = []
            for modifier_type, modifier_name, patch, allowed in requested:
                modifier = obj.modifiers.get(modifier_name)
                if modifier is not None:
                    if modifier.type != modifier_type:
                        raise ValueError(f"Modifier '{modifier_name}' is {modifier.type}, not {modifier_type}")
                    if existing_policy == "ERROR":
                        raise ValueError(f"Modifier '{modifier_name}' already exists")
                    if cloth_modifier.point_cache.is_baked and list(obj.modifiers).index(modifier) < list(
                        obj.modifiers
                    ).index(cloth_modifier):
                        raise ValueError("Cannot move an upstream finishing modifier across a baked Cloth modifier")
                    snapshots.append(
                        (
                            modifier,
                            list(obj.modifiers).index(modifier),
                            {name: getattr(modifier, name) for name in allowed},
                        )
                    )
                    reused.append(modifier.name)
                else:
                    modifier = obj.modifiers.new(name=modifier_name, type=modifier_type)
                    created.append(modifier)
                    ownership.append(_tag_owned_component(obj, modifier, "render_finish"))
                changes = _patch_rna(modifier, patch, allowed)
                _move_modifier_immediately_after(obj, modifier, preceding)
                if modifier_type == "CORRECTIVE_SMOOTH" and modifier.rest_source == "BIND":
                    scene.frame_set(rest_frame)
                    view_layer.update()
                    was_bound = modifier.is_bind
                    _bind_corrective_smooth(obj, modifier)
                    if not was_bound:
                        bound_during_request.append(modifier)
                preceding = modifier
                records.append({"modifier": _modifier_info(obj, modifier), "changes": changes})
            _tag_update(obj)
            after = _evaluated_geometry_evidence(obj)
        except Exception:
            for modifier in reversed(bound_during_request):
                with contextlib.suppress(Exception):
                    _unbind_corrective_smooth(obj, modifier)
            for record in reversed(ownership):
                with contextlib.suppress(Exception):
                    del obj[record["object_property"]]
            for modifier in reversed(created):
                with contextlib.suppress(Exception):
                    obj.modifiers.remove(modifier)
            for modifier, index, values in reversed(snapshots):
                for name, value in values.items():
                    with contextlib.suppress(Exception):
                        setattr(modifier, name, value)
                with contextlib.suppress(Exception):
                    obj.modifiers.move(list(obj.modifiers).index(modifier), index)
            raise
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()
        warnings = []
        if solidify is not None:
            thickness = abs(float(solidify.get("thickness", getattr(obj.modifiers[solidify_name], "thickness", 0))))
            edges = _edge_lengths(obj)
            if edges["median"] and thickness > edges["median"]:
                warnings.append("Solidify thickness exceeds the median simulation edge length and may self-intersect.")
            collision_distance = float(cloth_modifier.collision_settings.distance_min)
            if thickness > collision_distance * 2:
                warnings.append("Solidify thickness is more than twice the cloth object-collision distance.")
        if subdivision is not None and int(subdivision.get("render_levels", 0) or 0) > 3:
            warnings.append("Render subdivision above level 3 can multiply evaluated cloth surface cost sharply.")
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "cloth_modifier": cloth_modifier.name,
            "modifiers": records,
            "created_modifiers": [modifier.name for modifier in created],
            "reused_modifiers": reused,
            "modifier_stack": [_modifier_info(obj, modifier) for modifier in obj.modifiers],
            "geometry": {"before": before, "after": after},
            "base_mesh_preserved": True,
            "uv_layers_preserved": [layer.name for layer in obj.data.uv_layers],
            "material_slots_preserved": len(obj.material_slots),
            "motion_blur_configuration_changed": False,
            "rest_frame": rest_frame,
            "ownership": ownership,
            "warnings": warnings,
        }

    def export_cloth_simulation(
        self,
        scene_name,
        filepath,
        file_format,
        object_names,
        frame_start,
        frame_end,
        frame_step,
        coordinate_space,
        units,
        forward_axis,
        up_axis,
        topology_policy,
        evaluation_policy,
        include_uvs=True,
        include_normals=True,
        include_vertex_colors=True,
        include_materials=True,
        overwrite=False,
        max_frames=500,
    ):
        scene = bpy.data.scenes.get(scene_name)
        if scene is None:
            raise ValueError(f"Scene not found: {scene_name}")
        if file_format not in {"ALEMBIC", "USD"}:
            raise ValueError("file_format must be ALEMBIC or USD")
        if coordinate_space not in {"WORLD", "LOCAL"}:
            raise ValueError("coordinate_space must be WORLD or LOCAL")
        if units not in {"SCENE", *_EXPORT_UNIT_METERS}:
            raise ValueError("units must be SCENE, METERS, CENTIMETERS, or MILLIMETERS")
        if topology_policy not in {"REQUIRE_STABLE", "ALLOW_VARYING"}:
            raise ValueError("topology_policy must be REQUIRE_STABLE or ALLOW_VARYING")
        if evaluation_policy not in {"REQUIRE_BAKED", "EVALUATE"}:
            raise ValueError("evaluation_policy must be REQUIRE_BAKED or EVALUATE")
        valid_axes = {"X", "Y", "Z", "NEGATIVE_X", "NEGATIVE_Y", "NEGATIVE_Z"}
        if forward_axis not in valid_axes or up_axis not in valid_axes:
            raise ValueError("forward_axis and up_axis must be explicit signed X, Y, or Z axes")
        _validate_distinct_axes(forward_axis, up_axis)
        if frame_step <= 0 or frame_start > frame_end:
            raise ValueError("frame_step must be positive and frame_start must be <= frame_end")
        frame_count = (frame_end - frame_start) // frame_step + 1
        if not 1 <= frame_count <= max_frames <= 2_000:
            raise ValueError("Export frame count must be positive, within max_frames, and max_frames <= 2000")
        if not object_names or len(object_names) > 64 or len(set(object_names)) != len(object_names):
            raise ValueError("object_names must contain 1-64 unique object names")
        objects = [_get_object(name, {"MESH"}) for name in object_names]
        if any(obj.name not in scene.objects for obj in objects):
            raise ValueError("Every export object must be linked to the explicit scene")
        view_layer = next(
            (layer for layer in scene.view_layers if all(obj.name in layer.objects for obj in objects)),
            None,
        )
        if view_layer is None:
            raise ValueError("No scene view layer contains every export object")
        cloths = [(obj, modifier) for obj in objects for modifier in obj.modifiers if modifier.type == "CLOTH"]
        if not cloths:
            raise ValueError("At least one export object must have a Cloth modifier")
        if evaluation_policy == "REQUIRE_BAKED":
            unbaked = [f"{obj.name}:{modifier.name}" for obj, modifier in cloths if not modifier.point_cache.is_baked]
            if unbaked:
                raise ValueError(f"REQUIRE_BAKED found unbaked cloth caches: {unbaked}")
        resolved = os.path.abspath(bpy.path.abspath(filepath))
        if not os.path.isabs(resolved):
            raise ValueError("filepath must resolve to an absolute path")
        expected_extensions = {"ALEMBIC": {".abc"}, "USD": {".usd", ".usda", ".usdc"}}[file_format]
        extension = os.path.splitext(resolved)[1].lower()
        if extension not in expected_extensions:
            raise ValueError(f"{file_format} filepath must use one of {sorted(expected_extensions)}")
        parent = os.path.dirname(resolved)
        if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
            raise ValueError(f"Export parent directory must exist and be writable: {parent}")
        if os.path.exists(resolved) and not overwrite:
            raise ValueError("Export path already exists; set overwrite=True to replace it")
        if file_format == "ALEMBIC":
            if frame_step != 1:
                raise ValueError("Blender 5.1's Alembic exporter does not expose a frame-step option")
            if (forward_axis, up_axis) != ("NEGATIVE_Z", "Y"):
                raise ValueError("Blender 5.1's Alembic exporter has fixed NEGATIVE_Z forward and Y up orientation")

        frames = list(range(frame_start, frame_end + 1, frame_step))
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        original_range = (scene.frame_start, scene.frame_end, scene.frame_step)
        temporary_path = None
        topology_records = []
        stable_topology = True
        try:
            topology_records, stable_topology = _export_frame_topology(objects, scene, view_layer, frames)
            if topology_policy == "REQUIRE_STABLE" and not stable_topology:
                raise ValueError("Evaluated vertex/edge/face counts vary across the export frame range")
            descriptor, temporary_path = tempfile.mkstemp(prefix=".blendermcp-cloth-", suffix=extension, dir=parent)
            os.close(descriptor)
            os.unlink(temporary_path)
            _set_scene_frame_range(scene, frame_start, frame_end, frame_step)
            scene.frame_set(frame_start)
            with (
                bpy.context.temp_override(scene=scene, view_layer=view_layer),
                preserve_mode_and_selection(),
            ):
                for selected in list(bpy.context.selected_objects):
                    selected.select_set(False)
                for obj in objects:
                    obj.select_set(True)
                view_layer.objects.active = objects[0]
                if file_format == "ALEMBIC":
                    scene_scale = float(scene.unit_settings.scale_length) or 1.0
                    target_scale = scene_scale if units == "SCENE" else _EXPORT_UNIT_METERS[units]
                    result = bpy.ops.wm.alembic_export(
                        filepath=temporary_path,
                        start=frame_start,
                        end=frame_end,
                        selected=True,
                        flatten=coordinate_space == "WORLD",
                        uvs=include_uvs,
                        normals=include_normals,
                        vcolors=include_vertex_colors,
                        global_scale=scene_scale / target_scale,
                        export_custom_properties=True,
                        as_background_job=False,
                        evaluation_mode="RENDER",
                        init_scene_frame_range=False,
                    )
                else:
                    target_meters = (
                        float(scene.unit_settings.scale_length) or 1.0
                        if units == "SCENE"
                        else _EXPORT_UNIT_METERS[units]
                    )
                    result = bpy.ops.wm.usd_export(
                        filepath=temporary_path,
                        selected_objects_only=True,
                        export_animation=frame_count > 1,
                        export_uvmaps=include_uvs,
                        export_mesh_colors=include_vertex_colors,
                        export_normals=include_normals,
                        export_materials=include_materials,
                        export_custom_properties=True,
                        export_textures_mode="KEEP",
                        evaluation_mode="RENDER",
                        convert_orientation=True,
                        export_global_forward_selection=forward_axis,
                        export_global_up_selection=up_axis,
                        convert_scene_units="CUSTOM",
                        meters_per_unit=target_meters,
                        merge_parent_xform=coordinate_space == "WORLD",
                    )
            if "FINISHED" not in result:
                raise RuntimeError(f"{file_format} exporter did not finish: {sorted(result)}")
            if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) <= 0:
                raise RuntimeError(f"{file_format} exporter did not write a nonempty file")
            os.replace(temporary_path, resolved)
            temporary_path = None
        finally:
            _set_scene_frame_range(scene, *original_range)
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()
            if temporary_path and os.path.exists(temporary_path):
                with contextlib.suppress(OSError):
                    os.unlink(temporary_path)
        warnings = []
        if evaluation_policy == "EVALUATE":
            warnings.append("Export evaluation may have populated unbaked in-memory cloth caches.")
        if file_format == "ALEMBIC" and include_materials:
            warnings.append("Blender's Alembic exporter does not export Blender material networks.")
        return {
            "changed_objects": object_names if evaluation_policy == "EVALUATE" else [],
            "filepath": resolved,
            "format": file_format,
            "bytes": os.path.getsize(resolved),
            "scene": scene.name,
            "objects": object_names,
            "frame_range": {"start": frame_start, "end": frame_end, "step": frame_step, "count": frame_count},
            "coordinate_space": coordinate_space,
            "coordinate_space_contract": (
                "Parent hierarchy is flattened and transforms are written in world coordinates."
                if file_format == "ALEMBIC" and coordinate_space == "WORLD"
                else "Object-local geometry and parent hierarchy are retained."
                if coordinate_space == "LOCAL"
                else "USD object transforms preserve world placement; point data remains object-local."
            ),
            "units": units,
            "axes": {"forward": forward_axis, "up": up_axis},
            "topology": {
                "policy": topology_policy,
                "stable_counts": stable_topology,
                "per_frame": topology_records,
            },
            "attributes": {
                "uvs": include_uvs,
                "normals": include_normals,
                "vertex_colors": include_vertex_colors,
                "materials": include_materials if file_format == "USD" else False,
            },
            "evaluation_policy": evaluation_policy,
            "source_objects_and_caches_preserved": True,
            "warnings": warnings,
        }

    def analyze_cloth_performance(
        self,
        object_name,
        modifier_name,
        frames,
        warm_repeats=2,
        max_total_evaluations=60,
        include_short_bake=False,
        confirm_short_bake=False,
        short_bake_frame_start=None,
        short_bake_frame_end=None,
    ):
        obj, cloth_modifier = _get_cloth(object_name, modifier_name)
        normalized_frames = _validate_frames(frames, maximum=30)
        if not 1 <= warm_repeats <= 5:
            raise ValueError("warm_repeats must be in [1, 5]")
        total_evaluations = len(normalized_frames) * (1 + warm_repeats)
        if not 1 <= max_total_evaluations <= 180 or total_evaluations > max_total_evaluations:
            raise ValueError("Requested first/warm evaluations exceed max_total_evaluations")
        if include_short_bake and not confirm_short_bake:
            raise ValueError("include_short_bake requires confirm_short_bake=True")
        if not include_short_bake and (short_bake_frame_start is not None or short_bake_frame_end is not None):
            raise ValueError("Short-bake frame bounds require include_short_bake=True")
        short_bake_range = None
        if include_short_bake:
            if short_bake_frame_start is None or short_bake_frame_end is None:
                raise ValueError("Both short-bake frame bounds are required")
            short_bake_range = (int(short_bake_frame_start), int(short_bake_frame_end))
            if short_bake_range[0] > short_bake_range[1]:
                raise ValueError("short_bake_frame_start must be <= short_bake_frame_end")
            if short_bake_range[1] - short_bake_range[0] + 1 > 20:
                raise ValueError("The isolated short bake is limited to 20 frames")
        scene, view_layer = _scene_context_for_object(obj)
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe

        def timed_pass(pass_frames):
            timings = []
            for frame in pass_frames:
                started = time.perf_counter()
                scene.frame_set(frame)
                view_layer.update()
                evaluated = obj.evaluated_get(view_layer.depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    counts = [len(mesh.vertices), len(mesh.edges), len(mesh.polygons)]
                finally:
                    evaluated.to_mesh_clear()
                timings.append(
                    {
                        "frame": frame,
                        "seconds": time.perf_counter() - started,
                        "evaluated_counts": counts,
                    }
                )
            return timings

        temporary = None
        temporary_data = None
        short_bake = None
        try:
            first_pass = timed_pass(normalized_frames)
            warm_passes = [timed_pass(normalized_frames) for _repeat in range(warm_repeats)]
            cost_evidence = _modifier_cost_evidence(obj, cloth_modifier, view_layer.depsgraph)
            if short_bake_range is not None:
                short_bake_start, short_bake_end = short_bake_range
                temporary = obj.copy()
                temporary.name = f"__BlendMCP_Profile_{uuid.uuid4().hex[:8]}"
                temporary_data = obj.data.copy()
                temporary.data = temporary_data
                scene.collection.objects.link(temporary)
                view_layer.update()
                temporary_modifier = _get_modifier(temporary, modifier_name, "CLOTH")
                if temporary_modifier.point_cache.is_baked or temporary_modifier.point_cache.is_baking:
                    raise RuntimeError("Temporary profiling cache unexpectedly inherited active bake state")
                _configure_independent_cache(
                    temporary_modifier.point_cache,
                    temporary.name,
                    temporary_modifier.name,
                )
                _set_cache_frame_range(
                    temporary_modifier.point_cache,
                    short_bake_start,
                    short_bake_end,
                )
                scene.frame_set(short_bake_start)
                started = time.perf_counter()
                _run_point_cache_operator(temporary, temporary_modifier.point_cache, bpy.ops.ptcache.bake, bake=True)
                short_bake = {
                    "frames": short_bake_end - short_bake_start + 1,
                    "seconds": time.perf_counter() - started,
                    "point_cache": _cache_info(temporary_modifier.point_cache),
                    "isolated_temporary_object": True,
                }
        finally:
            if temporary is not None:
                with contextlib.suppress(Exception):
                    temporary_modifier = _get_modifier(temporary, modifier_name, "CLOTH")
                    if temporary_modifier.point_cache.is_baked:
                        _run_point_cache_operator(
                            temporary,
                            temporary_modifier.point_cache,
                            bpy.ops.ptcache.free_bake,
                        )
                _remove_created_object(temporary, temporary_data)
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()
        first_total = sum(record["seconds"] for record in first_pass)
        warm_totals = [sum(record["seconds"] for record in records) for records in warm_passes]
        recommendations = []
        if cost_evidence["self_collision"]:
            recommendations.append("Use a bounded self-collision vertex group or disable self-collision for previews.")
        if cost_evidence["collider_evaluated_faces"] > cost_evidence["base_geometry"]["faces"] * 4:
            recommendations.append("Use simpler collision proxies; collider face count dominates the cloth surface.")
        if cost_evidence["solver_quality"] > 8 or cost_evidence["collision_quality"] > 4:
            recommendations.append(
                "Lower solver/collision quality for preview variants and restore it for final baking."
            )
        if cost_evidence["topology_changing_modifiers"]:
            recommendations.append("Move or simplify topology-changing modifiers around Cloth where the shot permits.")
        if not recommendations:
            recommendations.append(
                "No dominant structural cost flag was detected; profile representative contact frames."
            )
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier_name,
            "frames": normalized_frames,
            "timings": {
                "first_pass": first_pass,
                "first_pass_seconds": first_total,
                "warm_passes": warm_passes,
                "warm_pass_seconds": warm_totals,
                "first_pass_is_cold_guaranteed": False,
                "note": "Existing point-cache state is preserved, so the first pass is not forcibly cold.",
            },
            "short_isolated_bake": short_bake,
            "cost_evidence": cost_evidence,
            "solver_result": _read_fields(
                cloth_modifier.solver_result,
                {
                    prop.identifier
                    for prop in cloth_modifier.solver_result.bl_rna.properties
                    if prop.identifier != "rna_type"
                },
            )
            if cloth_modifier.solver_result
            else None,
            "point_cache": _cache_info(cloth_modifier.point_cache),
            "source_cache_freed_or_overwritten": False,
            "recommendations": recommendations,
            "warnings": ["Frame evaluation can populate the source object's in-memory cloth cache."],
        }
