# Blender RNA is dynamic and these handlers are also imported by the bpy-free
# unit-test harness, so annotations remain deliberately structural.
# ruff: file-ignore[magic-value-comparison, missing-return-type-private-function, missing-return-type-undocumented-public-function, missing-type-function-argument, no-self-use, too-many-arguments, too-many-branches, too-many-locals, too-many-positional-arguments, too-many-statements, too-many-statements-in-try-clause, undocumented-public-method]
"""Blender-main-thread handlers for phase-one Mantaflow liquid workflows."""

from __future__ import annotations

import contextlib
import itertools
import json
import math
import os
import time

import bpy
import mathutils

from ..helpers import preserve_mode_and_selection, set_active
from .liquid import (
    _CACHE_FIELDS,
    _CACHE_FLAGS,
    _ensure_collection,
    _finite,
    _get_domain,
    _get_object,
    _get_role,
    _link_object,
    _object_in_collection,
    _patch_rna,
    _read_fields,
    _reject_baked,
    _resolved_cache_path,
    _restore_rna,
    _serialize,
    _validate_rna_value,
    _world_bounds,
)

_MESH_FIELDS = {
    "use_mesh",
    "mesh_scale",
    "mesh_particle_radius",
    "mesh_smoothen_pos",
    "mesh_smoothen_neg",
    "mesh_concave_upper",
    "mesh_concave_lower",
    "mesh_generator",
    "use_speed_vectors",
    "cache_mesh_format",
}
_SECONDARY_FIELDS = {
    "use_spray_particles",
    "use_foam_particles",
    "use_bubble_particles",
    "use_tracer_particles",
    "sndparticle_combined_export",
    "sndparticle_boundary",
    "sndparticle_life_min",
    "sndparticle_life_max",
    "sndparticle_potential_min_wavecrest",
    "sndparticle_potential_max_wavecrest",
    "sndparticle_potential_min_trappedair",
    "sndparticle_potential_max_trappedair",
    "sndparticle_potential_min_energy",
    "sndparticle_potential_max_energy",
    "sndparticle_sampling_wavecrest",
    "sndparticle_sampling_trappedair",
    "sndparticle_update_radius",
    "sndparticle_bubble_buoyancy",
    "sndparticle_bubble_drag",
    "particle_scale",
}
_DIFFUSION_FIELDS = {
    "use_diffusion",
    "viscosity_base",
    "viscosity_exponent",
    "use_viscosity",
    "viscosity_value",
    "surface_tension",
}
_FLOW_ANIMATION_FIELDS = {
    "use_inflow",
    "use_initial_velocity",
    "velocity_factor",
    "velocity_normal",
    "velocity_random",
}
_GUIDE_DOMAIN_FIELDS = {"use_guide", "guide_source", "guide_alpha", "guide_beta", "guide_vel_factor"}
_WEIGHT_FIELDS = {
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
    "boid",
    "turbulence",
    "drag",
    "smokeflow",
}
_FIELD_FIELDS = {
    "type",
    "strength",
    "shape",
    "falloff_type",
    "noise",
    "seed",
    "use_min_distance",
    "distance_min",
    "use_max_distance",
    "distance_max",
}
_CACHE_CONFIG_FIELDS = {
    "cache_directory",
    "cache_type",
    "cache_data_format",
    "cache_mesh_format",
    "cache_particle_format",
    "cache_frame_start",
    "cache_frame_end",
    "cache_frame_offset",
    "cache_resumable",
}
_SECONDARY_TOGGLES = (
    "use_spray_particles",
    "use_foam_particles",
    "use_bubble_particles",
    "use_tracer_particles",
)
_LIQUID_PRESETS = {
    "WATER": {
        "use_diffusion": True,
        "viscosity_base": 1.0,
        "viscosity_exponent": 6,
        "use_viscosity": False,
        "viscosity_value": 1.0,
        "surface_tension": 0.0,
    },
    "OIL": {
        "use_diffusion": True,
        "viscosity_base": 5.0,
        "viscosity_exponent": 5,
        "use_viscosity": False,
        "viscosity_value": 1.0,
        "surface_tension": 0.2,
    },
    "HONEY": {
        "use_diffusion": True,
        "viscosity_base": 2.0,
        "viscosity_exponent": 3,
        "use_viscosity": True,
        "viscosity_value": 2.0,
        "surface_tension": 0.8,
    },
    "MOLTEN": {
        "use_diffusion": True,
        "viscosity_base": 5.0,
        "viscosity_exponent": 3,
        "use_viscosity": True,
        "viscosity_value": 1.5,
        "surface_tension": 1.0,
    },
    "STYLIZED": {
        "use_diffusion": True,
        "viscosity_base": 1.0,
        "viscosity_exponent": 2,
        "use_viscosity": True,
        "viscosity_value": 2.0,
        "surface_tension": 2.0,
    },
}
_MATERIAL_PRESETS = {
    "WATER": {
        "base_color": (0.92, 0.98, 1.0, 1.0),
        "transmission_weight": 1.0,
        "ior": 1.333,
        "roughness": 0.04,
        "volume_absorption_color": (0.75, 0.95, 1.0, 1.0),
        "volume_density": 0.02,
    },
    "GLASS": {
        "base_color": (1.0, 1.0, 1.0, 1.0),
        "transmission_weight": 1.0,
        "ior": 1.45,
        "roughness": 0.02,
        "volume_absorption_color": (1.0, 1.0, 1.0, 1.0),
        "volume_density": 0.0,
    },
    "OIL": {
        "base_color": (0.55, 0.32, 0.08, 1.0),
        "transmission_weight": 0.82,
        "ior": 1.47,
        "roughness": 0.12,
        "volume_absorption_color": (0.35, 0.12, 0.02, 1.0),
        "volume_density": 0.15,
    },
    "TINTED": {
        "base_color": (0.1, 0.45, 0.8, 1.0),
        "transmission_weight": 0.95,
        "ior": 1.36,
        "roughness": 0.08,
        "volume_absorption_color": (0.04, 0.22, 0.7, 1.0),
        "volume_density": 0.12,
    },
}


def _cache_state(settings):
    return {
        "configuration": _read_fields(settings, _CACHE_FIELDS),
        "stages": {name: bool(getattr(settings, name, False)) for name in _CACHE_FLAGS},
    }


def _active_cache_flags(settings, names=None):
    candidates = names or _CACHE_FLAGS
    return [name for name in candidates if bool(getattr(settings, name, False))]


def _reject_cache_flags(settings, names, reason):
    active = _active_cache_flags(settings, names)
    if active:
        raise ValueError(f"{reason}; free or finish the exact cache stages first: {active}")


def _update_or_restore(obj, owner, changes):
    try:
        obj.update_tag(refresh={"DATA"})
        bpy.context.view_layer.update()
    except Exception:
        _restore_rna(owner, changes)
        raise


def _estimate_mesh_output(obj, settings):
    bounds = _world_bounds(obj)
    longest = max(bounds["dimensions"])
    cell = longest / settings.resolution_max if longest > 0 else 0.0
    scale = int(settings.mesh_scale)
    dimensions = [max(1, math.ceil(value / cell) * scale) if cell > 0 else 0 for value in bounds["dimensions"]]
    return {
        "base_resolution_max": int(settings.resolution_max),
        "mesh_scale": scale,
        "estimated_longest_axis_resolution": int(settings.resolution_max) * scale,
        "estimated_cells_xyz": dimensions,
        "base_cell_size_world": cell,
        "note": "Grid dimensions are geometric estimates, not occupied cells or output polygon counts.",
    }


def _expand_viscosity_config(config):
    config = dict(config or {})
    preset = config.pop("preset", None)
    dynamic = config.pop("dynamic_viscosity_pa_s", None)
    density = config.pop("density_kg_m3", None)
    source = "DIRECT"
    conversion = None
    if preset is not None:
        if preset not in _LIQUID_PRESETS:
            raise ValueError(f"Unknown viscosity preset: {preset}")
        expanded = dict(_LIQUID_PRESETS[preset])
        expanded.update(config)
        source = f"PRESET:{preset}"
    elif dynamic is not None or density is not None:
        if dynamic is None or density is None or dynamic <= 0 or density <= 0:
            raise ValueError("Positive dynamic_viscosity_pa_s and density_kg_m3 are both required")
        kinematic = float(dynamic) / float(density)
        exponent = max(0, min(10, math.ceil(-math.log10(kinematic))))
        base = kinematic * (10**exponent)
        if not 0.0 < base <= 10.0:
            raise ValueError("Converted kinematic viscosity is outside Blender's base/exponent range [1e-10, 10]")
        expanded = {
            "use_diffusion": True,
            "viscosity_base": base,
            "viscosity_exponent": exponent,
            **config,
        }
        source = "SI_DYNAMIC_DENSITY"
        conversion = {
            "dynamic_viscosity_pa_s": dynamic,
            "density_kg_m3": density,
            "kinematic_viscosity_m2_s": kinematic,
            "formula": "dynamic_viscosity_pa_s / density_kg_m3",
        }
    else:
        expanded = config
    return expanded, source, conversion


def _action_fcurves(owner):
    owner_id = getattr(owner, "id_data", owner)
    animation = getattr(owner_id, "animation_data", None)
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


def _key_points(owner, data_path, frame):
    return [
        (curve, point)
        for curve in _action_fcurves(owner)
        if curve.data_path == data_path
        for point in curve.keyframe_points
        if abs(float(point.co[0]) - frame) <= 1e-6
    ]


def _snapshot_point(point):
    return {
        "co": list(point.co),
        "interpolation": point.interpolation,
        "easing": point.easing,
        "handle_left": list(point.handle_left),
        "handle_right": list(point.handle_right),
        "handle_left_type": point.handle_left_type,
        "handle_right_type": point.handle_right_type,
    }


def _restore_point(point, snapshot):
    for name, value in snapshot.items():
        setattr(point, name, value)


def _scene_context_for_object(obj):
    scenes = [scene for scene in bpy.data.scenes if obj.name in scene.objects]
    if not scenes:
        raise ValueError(f"Object '{obj.name}' is not linked to a scene")
    scene = bpy.context.scene if bpy.context.scene in scenes else scenes[0]
    view_layer = next((layer for layer in scene.view_layers if obj.name in layer.objects), None)
    if view_layer is None:
        raise ValueError(f"Object '{obj.name}' is excluded from every view layer in scene '{scene.name}'")
    return scene, view_layer


def _run_fluid_operator(obj, operator):
    scene, view_layer = _scene_context_for_object(obj)
    with preserve_mode_and_selection():
        set_active(obj)
        if obj.mode != "OBJECT":
            result = bpy.ops.object.mode_set(mode="OBJECT")
            if "FINISHED" not in result:
                raise RuntimeError(f"Could not enter Object Mode for fluid cache operation: {sorted(result)}")
        with bpy.context.temp_override(
            scene=scene,
            view_layer=view_layer,
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            result = operator()
    if "FINISHED" not in result:
        raise RuntimeError(f"Fluid cache operator did not finish: {sorted(result)}")
    return result


def _cache_directory_evidence(path, max_entries=10_000):
    resolved = _resolved_cache_path(path)
    exists = os.path.isdir(resolved)
    entries = 0
    bytes_total = 0
    truncated = False
    if exists:
        for root, _directories, filenames in os.walk(resolved):
            for filename in filenames:
                entries += 1
                if entries > max_entries:
                    truncated = True
                    break
                with contextlib.suppress(OSError):
                    bytes_total += os.path.getsize(os.path.join(root, filename))
            if truncated:
                break
    return {
        "configured": path,
        "resolved": resolved,
        "exists": exists,
        "writable": exists and os.access(resolved, os.W_OK),
        "files_scanned": min(entries, max_entries),
        "bytes_scanned": bytes_total,
        "scan_truncated": truncated,
    }


def _field_snapshot(obj):
    return {
        "matrix_basis": obj.matrix_basis.copy(),
        "settings": _read_fields(obj.field, _FIELD_FIELDS),
    }


def _create_force_field(scene, view_layer, collection, spec):
    with preserve_mode_and_selection(), bpy.context.temp_override(scene=scene, view_layer=view_layer):
        result = bpy.ops.object.effector_add(
            type=spec["field_type"],
            location=spec["location"],
            rotation=spec["rotation_euler"],
        )
        obj = bpy.context.active_object
    if "FINISHED" not in result or obj is None or obj.field is None:
        raise RuntimeError(f"Blender did not create force field '{spec['object_name']}': {sorted(result)}")
    obj.name = spec["object_name"]
    if obj.name not in collection.objects:
        collection.objects.link(obj)
    for linked_collection in list(obj.users_collection):
        if linked_collection != collection:
            linked_collection.objects.unlink(obj)
    return obj


def _restore_field(obj, snapshot):
    obj.matrix_basis = snapshot["matrix_basis"]
    for name, value in snapshot["settings"].items():
        with contextlib.suppress(Exception):
            setattr(obj.field, name, value)


def _set_cache_range(settings, start, end):
    if start > end:
        raise ValueError("cache_frame_start must be <= cache_frame_end")
    _validate_rna_value(settings, "cache_frame_start", start)
    _validate_rna_value(settings, "cache_frame_end", end)
    if start > settings.cache_frame_end:
        settings.cache_frame_end = end
        settings.cache_frame_start = start
    else:
        settings.cache_frame_start = start
        settings.cache_frame_end = end


def _configure_principled_material(material, values):
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.inputs["Base Color"].default_value = values["base_color"]
    principled.inputs["Transmission Weight"].default_value = values["transmission_weight"]
    principled.inputs["IOR"].default_value = values["ior"]
    principled.inputs["Roughness"].default_value = values["roughness"]
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    if values["volume_density"] > 0:
        volume = nodes.new("ShaderNodeVolumeAbsorption")
        volume.inputs["Color"].default_value = values["volume_absorption_color"]
        volume.inputs["Density"].default_value = values["volume_density"]
        material.node_tree.links.new(volume.outputs["Volume"], output.inputs["Volume"])
    return {"surface_node": principled.name, "output_node": output.name}


def _octahedron_mesh(name):
    mesh = bpy.data.meshes.new(f"{name} Mesh")
    vertices = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    faces = [
        (0, 2, 4),
        (2, 1, 4),
        (1, 3, 4),
        (3, 0, 4),
        (2, 0, 5),
        (1, 2, 5),
        (3, 1, 5),
        (0, 3, 5),
    ]
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    return mesh


def _particle_role(system):
    label = f"{system.name} {getattr(system.settings, 'name', '')}".lower()
    roles = [role for role in ("spray", "foam", "bubble", "tracer") if role in label]
    return "+".join(role.upper() for role in roles) if roles else "UNKNOWN"


def _evaluated_output(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        count = len(mesh.vertices)
        sampled = (
            list(mesh.vertices)
            if count <= 200_000
            else [mesh.vertices[index] for index in range(0, count, math.ceil(count / 200_000))]
        )
        world = [evaluated.matrix_world @ vertex.co for vertex in sampled]
        finite = all(math.isfinite(float(value)) for point in world for value in point)
        bounds = None
        if world:
            minimum = [min(float(point[axis]) for point in world) for axis in range(3)]
            maximum = [max(float(point[axis]) for point in world) for axis in range(3)]
            bounds = {
                "coordinate_space": "WORLD",
                "minimum": minimum,
                "maximum": maximum,
                "dimensions": [maximum[axis] - minimum[axis] for axis in range(3)],
            }
        return {
            "vertices": count,
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "sampled_vertices": len(sampled),
            "finite": finite,
            "bounds": bounds,
        }
    finally:
        evaluated.to_mesh_clear()


class LiquidPhaseOneHandlersMixin:
    """Provide liquid appearance, motion, guiding, rendering, and cache lifecycle handlers."""

    def configure_liquid_mesh(self, domain_object_name, modifier_name, patch):
        obj, modifier, settings = _get_domain(domain_object_name, modifier_name)
        if not patch:
            raise ValueError("Mesh patch cannot be empty")
        _reject_cache_flags(
            settings,
            ("has_cache_baked_mesh", "is_cache_baking_mesh", "is_cache_baking_any"),
            "Cannot change liquid mesh settings while the mesh stage is baked or baking",
        )
        if "use_speed_vectors" in patch:
            _reject_cache_flags(
                settings,
                ("has_cache_baked_data", "has_cache_baked_mesh", "is_cache_baking_any"),
                "Speed vectors must be configured before data or mesh baking",
            )
        changes = _patch_rna(settings, patch, _MESH_FIELDS)
        _update_or_restore(obj, settings, changes)
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "estimated_output": _estimate_mesh_output(obj, settings),
            "cache": _cache_state(settings),
            "invalidated_cache_stages": ["MESH"],
            "data_rebake_required": "use_speed_vectors" in changes,
            "next_required_bake_stage": "DATA" if "use_speed_vectors" in changes else "MESH",
            "retained_live_modifier": True,
        }

    def configure_liquid_secondary_particles(self, domain_object_name, modifier_name, patch):
        obj, modifier, settings = _get_domain(domain_object_name, modifier_name)
        if not patch:
            raise ValueError("Secondary-particle patch cannot be empty")
        _reject_baked(settings)
        prospective = {name: patch.get(name, getattr(settings, name)) for name in _SECONDARY_FIELDS}
        pairs = (
            ("sndparticle_life_min", "sndparticle_life_max"),
            ("sndparticle_potential_min_wavecrest", "sndparticle_potential_max_wavecrest"),
            ("sndparticle_potential_min_trappedair", "sndparticle_potential_max_trappedair"),
            ("sndparticle_potential_min_energy", "sndparticle_potential_max_energy"),
        )
        for minimum, maximum in pairs:
            if prospective[minimum] > prospective[maximum]:
                raise ValueError(f"{minimum} must be <= {maximum}")
        changes = _patch_rna(settings, patch, _SECONDARY_FIELDS)
        _update_or_restore(obj, settings, changes)
        enabled = [
            name.removeprefix("use_").removesuffix("_particles").upper()
            for name in _SECONDARY_TOGGLES
            if getattr(settings, name)
        ]
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "changes": changes,
            "enabled_particle_types": enabled,
            "combined_export": settings.sndparticle_combined_export,
            "combined_export_semantics": (
                "OFF creates separate eligible systems; another value combines the named roles into one output."
            ),
            "invalidated_cache_stages": ["DATA", "PARTICLES"],
            "next_required_bake_stage": "DATA",
            "warnings": ["Secondary particles increase simulation, cache, and render cost."] if enabled else [],
        }

    def configure_liquid_diffusion(self, domain_object_name, modifier_name, config):
        obj, modifier, settings = _get_domain(domain_object_name, modifier_name)
        if not config:
            raise ValueError("Diffusion config cannot be empty")
        _reject_baked(settings)
        patch, source, conversion = _expand_viscosity_config(config)
        if not patch:
            raise ValueError("Diffusion config does not contain a setting")
        changes = _patch_rna(settings, patch, _DIFFUSION_FIELDS)
        _update_or_restore(obj, settings, changes)
        kinematic = float(settings.viscosity_base) * (10.0 ** (-int(settings.viscosity_exponent)))
        warnings = []
        if settings.use_viscosity and settings.viscosity_value >= 5:
            warnings.append("Very high viscosity can require smaller time steps and may destabilize the solve.")
        warnings.append("Viscosity is scene-scale sensitive and does not turn liquid into rigid-body material.")
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "preset_schema_version": 1,
            "source": source,
            "expanded_values": patch,
            "si_conversion": conversion,
            "represented_kinematic_viscosity_m2_s": kinematic,
            "changes": changes,
            "invalidated_cache_stages": ["DATA", "MESH", "PARTICLES"],
            "warnings": warnings,
        }

    def animate_liquid_flow(
        self,
        object_name,
        modifier_name,
        domain_object_name,
        keyframes,
        policy="INSERT_ONLY",
        subframes=None,
    ):
        obj, modifier, flow = _get_role(object_name, modifier_name, "FLOW")
        domain_obj, _domain_modifier, domain = _get_domain(domain_object_name)
        _reject_baked(domain)
        if policy not in {"INSERT_ONLY", "REPLACE_EXISTING"}:
            raise ValueError("policy must be INSERT_ONLY or REPLACE_EXISTING")
        if not keyframes or len(keyframes) > 500:
            raise ValueError("keyframes must contain 1-500 records")
        if domain.fluid_group is not None and not _object_in_collection(obj, domain.fluid_group):
            raise ValueError(f"Flow '{obj.name}' is outside domain collection '{domain.fluid_group.name}'")
        if subframes is not None:
            _validate_rna_value(flow, "subframes", subframes)
        resolved = []
        identities = set()
        for index, record in enumerate(keyframes):
            frame = float(record["frame"])
            if not math.isfinite(frame) or not -1_000_000 <= frame <= 1_000_000:
                raise ValueError(f"Keyframe {index} has an invalid frame")
            properties = set(record) & _FLOW_ANIMATION_FIELDS
            if len(properties) != 1:
                raise ValueError(f"Keyframe {index} must set exactly one flow property")
            property_name = properties.pop()
            value = record[property_name]
            prop = flow.bl_rna.properties.get(property_name)
            if prop is None or prop.is_readonly or not prop.is_animatable:
                raise ValueError(
                    f"FluidFlowSettings.{property_name} is not keyable in Blender {bpy.app.version_string}"
                )
            _validate_rna_value(flow, property_name, value)
            identity = (property_name, frame)
            if identity in identities:
                raise ValueError(f"Duplicate keyframe for {property_name} at {frame:g}")
            identities.add(identity)
            path = flow.path_from_id(property_name)
            existing = _key_points(flow, path, frame)
            if policy == "INSERT_ONLY" and existing:
                raise ValueError(f"A key already exists for {property_name} at frame {frame:g}")
            resolved.append(
                {
                    "property": property_name,
                    "value": value,
                    "frame": frame,
                    "path": path,
                    "interpolation": record.get("interpolation", "CONSTANT"),
                    "old_value": _serialize(getattr(flow, property_name)),
                    "existing": [(curve, point, _snapshot_point(point)) for curve, point in existing],
                }
            )
        old_subframes = flow.subframes
        applied = []
        try:
            if subframes is not None:
                flow.subframes = subframes
            for entry in resolved:
                applied.append(entry)
                setattr(flow, entry["property"], entry["value"])
                inserted = flow.keyframe_insert(data_path=entry["property"], frame=entry["frame"], group="Liquid MCP")
                if not inserted:
                    raise RuntimeError(f"Blender did not insert {entry['property']} at frame {entry['frame']:g}")
                points = _key_points(flow, entry["path"], entry["frame"])
                if not points:
                    raise RuntimeError("Inserted flow keyframe could not be found in the object action")
                for curve, point in points:
                    point.interpolation = entry["interpolation"]
                    curve.update()
            obj.update_tag(refresh={"DATA"})
            bpy.context.view_layer.update()
        except Exception:
            flow.subframes = old_subframes
            for entry in reversed(applied):
                with contextlib.suppress(Exception):
                    setattr(flow, entry["property"], entry["old_value"])
                if entry["existing"]:
                    for curve, point, snapshot in entry["existing"]:
                        with contextlib.suppress(Exception):
                            _restore_point(point, snapshot)
                            curve.update()
                else:
                    with contextlib.suppress(Exception):
                        flow.keyframe_delete(data_path=entry["property"], frame=entry["frame"])
            raise
        animation = getattr(obj, "animation_data", None)
        action = getattr(animation, "action", None)
        keyed = [
            {
                "property": entry["property"],
                "data_path": entry["path"],
                "frame": entry["frame"],
                "value": entry["value"],
                "interpolation": entry["interpolation"],
            }
            for entry in resolved
        ]
        warnings = ["Flow animation invalidates data, mesh, and particle cache stages."]
        if flow.flow_behavior == "GEOMETRY":
            warnings.append("GEOMETRY is a one-shot source; use INFLOW for continuous emission scheduling.")
        if any(item["property"] == "use_inflow" for item in keyed) and flow.flow_behavior != "INFLOW":
            warnings.append("use_inflow keys have no continuous-emission meaning unless flow_behavior is INFLOW.")
        return {
            "changed_objects": [obj.name, domain_obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "domain": domain_obj.name,
            "action": action.name if action else None,
            "action_slot": getattr(getattr(animation, "action_slot", None), "identifier", None),
            "policy": policy,
            "subframes": int(flow.subframes),
            "keyframes": keyed,
            "invalidated_cache_stages": ["DATA", "MESH", "PARTICLES"],
            "warnings": warnings,
        }

    def create_liquid_guide(
        self,
        domain_object_name,
        domain_modifier_name,
        guide_object_name,
        source="EFFECTOR",
        guide_modifier_name="Liquid Guide",
        existing_policy="ERROR",
        guide_mode="OVERRIDE",
        velocity_factor=1.0,
        guide_parent_domain_object_name=None,
        guide_collection_name=None,
        cache_frame_start=None,
        cache_frame_end=None,
        guide_alpha=None,
        guide_beta=None,
        guide_vel_factor=None,
    ):
        domain_obj, domain_modifier, domain = _get_domain(domain_object_name, domain_modifier_name)
        _reject_baked(domain)
        if source not in {"EFFECTOR", "DOMAIN"}:
            raise ValueError("source must be EFFECTOR or DOMAIN")
        start = domain.cache_frame_start if cache_frame_start is None else int(cache_frame_start)
        end = domain.cache_frame_end if cache_frame_end is None else int(cache_frame_end)
        if start > end:
            raise ValueError("cache_frame_start must be <= cache_frame_end")
        if source == "EFFECTOR" and guide_parent_domain_object_name is not None:
            raise ValueError("guide_parent_domain_object_name is valid only for DOMAIN guide sources")
        if source == "DOMAIN" and not guide_parent_domain_object_name:
            raise ValueError("DOMAIN guide sources require guide_parent_domain_object_name")
        old_domain = {
            name: getattr(domain, name)
            for name in (
                *_GUIDE_DOMAIN_FIELDS,
                "guide_parent",
                "effector_group",
                "cache_frame_start",
                "cache_frame_end",
            )
        }
        guide_result = None
        linked = False
        created_collection_link = False
        try:
            if source == "EFFECTOR":
                guide = _get_object(guide_object_name, {"MESH"})
                if guide_collection_name:
                    scene, _view_layer = _scene_context_for_object(domain_obj)
                    collection, _created, created_collection_link = _ensure_collection(scene, guide_collection_name)
                    linked = _link_object(collection, guide)
                    domain.effector_group = collection
                guide_result = self.add_liquid_effector(
                    object_name=guide_object_name,
                    domain_object_name=domain_object_name,
                    modifier_name=guide_modifier_name,
                    existing_policy=existing_policy,
                    effector_type="GUIDE",
                    settings={"guide_mode": guide_mode, "velocity_factor": velocity_factor},
                )
                domain.use_guide = True
                domain.guide_source = "EFFECTOR"
                domain.guide_parent = None
            else:
                parent_obj, _parent_modifier, _parent = _get_domain(guide_parent_domain_object_name)
                if parent_obj == domain_obj:
                    raise ValueError("A liquid domain cannot guide itself")
                guide_object = _get_object(guide_object_name)
                if guide_object != parent_obj:
                    raise ValueError("For DOMAIN guides, guide_object_name must identify the parent domain")
                domain.use_guide = True
                domain.guide_source = "DOMAIN"
                domain.guide_parent = parent_obj
            for name, value in (
                ("guide_alpha", guide_alpha),
                ("guide_beta", guide_beta),
                ("guide_vel_factor", guide_vel_factor),
            ):
                if value is not None:
                    _validate_rna_value(domain, name, value)
                    setattr(domain, name, value)
            _set_cache_range(domain, start, end)
            bpy.context.view_layer.update()
        except Exception:
            for name, value in old_domain.items():
                if name in {"cache_frame_start", "cache_frame_end"}:
                    continue
                with contextlib.suppress(Exception):
                    setattr(domain, name, value)
            with contextlib.suppress(Exception):
                _set_cache_range(domain, old_domain["cache_frame_start"], old_domain["cache_frame_end"])
            if linked:
                with contextlib.suppress(Exception):
                    collection.objects.unlink(guide)  # pyright: ignore[reportArgumentType]
            if created_collection_link:
                with contextlib.suppress(Exception):
                    scene.collection.children.unlink(collection)  # pyright: ignore[reportArgumentType]
            raise
        return {
            "changed_objects": sorted({domain_obj.name, guide_object_name}),
            "domain": domain_obj.name,
            "domain_modifier": domain_modifier.name,
            "source": source,
            "guide_object": guide_object_name,
            "guide_setup": guide_result,
            "settings": _read_fields(domain, _GUIDE_DOMAIN_FIELDS | {"guide_parent"}),
            "frame_range": [domain.cache_frame_start, domain.cache_frame_end],
            "required_bake_order": ["GUIDES", "DATA", "MESH/PARTICLES"],
            "invalidated_cache_stages": ["GUIDES", "DATA", "MESH", "PARTICLES"],
        }

    def configure_liquid_force_fields(
        self,
        scene_name,
        domain_object_name,
        modifier_name,
        fields,
        force_collection_name,
        create_collection=False,
        weights=None,
    ):
        scene = bpy.data.scenes.get(scene_name)
        if scene is None:
            raise ValueError(f"Scene not found: {scene_name}")
        domain_obj, modifier, domain = _get_domain(domain_object_name, modifier_name)
        if domain_obj.name not in scene.objects:
            raise ValueError(f"Domain '{domain_obj.name}' is not linked to scene '{scene.name}'")
        _reject_baked(domain)
        if not fields and not weights:
            raise ValueError("Provide at least one force field or an effector-weights patch")
        if len(fields) > 64 or len({item["object_name"] for item in fields}) != len(fields):
            raise ValueError("fields must contain at most 64 unique object names")
        collection = bpy.data.collections.get(force_collection_name)
        collection_created_link = False
        if collection is None:
            if not create_collection:
                raise ValueError(f"Collection not found: {force_collection_name}")
            collection, _created, collection_created_link = _ensure_collection(scene, force_collection_name)
        elif collection != scene.collection and collection not in scene.collection.children_recursive:
            raise ValueError(f"Collection '{collection.name}' is not linked to scene '{scene.name}'")
        view_layer = next((layer for layer in scene.view_layers if domain_obj.name in layer.objects), None)
        if view_layer is None:
            raise ValueError(f"Domain '{domain_obj.name}' is excluded from every view layer in scene '{scene.name}'")
        resolved = []
        for spec in fields:
            name = spec["object_name"]
            obj = bpy.data.objects.get(name)
            if obj is None and not spec.get("create_if_missing"):
                raise ValueError(f"Force-field object not found: {name}")
            if obj is not None and obj.name not in scene.objects:
                raise ValueError(f"Force-field object '{name}' is not linked to scene '{scene.name}'")
            if obj is not None and obj.field is None:
                raise ValueError(
                    f"Object '{name}' has no FieldSettings; use a Blender force-field object or choose a new name "
                    "with create_if_missing=True"
                )
            for vector_name in ("location", "rotation_euler"):
                vector = spec[vector_name]
                _finite(vector, vector_name)
                if len(vector) != 3:
                    raise ValueError(f"{vector_name} must contain three finite values")
            prospective = {
                "type": spec["field_type"],
                **{name: spec[name] for name in _FIELD_FIELDS if name in spec and name != "type"},
            }
            if obj is not None:
                for name, value in prospective.items():
                    _validate_rna_value(obj.field, name, value)
            resolved.append((obj, spec, prospective))
        weight_changes = {}
        linked_objects = []
        created_objects = []
        snapshots = []
        old_collection = domain.force_collection
        try:
            for resolved_obj, spec, prospective in resolved:
                obj = resolved_obj
                if obj is None:
                    obj = _create_force_field(scene, view_layer, collection, spec)
                    created_objects.append(obj)
                else:
                    snapshots.append((obj, _field_snapshot(obj)))
                    if _link_object(collection, obj):
                        linked_objects.append(obj)
                world_scale = obj.matrix_world.to_scale()
                obj.matrix_world = mathutils.Matrix.LocRotScale(
                    spec["location"],
                    mathutils.Euler(spec["rotation_euler"]).to_quaternion(),
                    tuple(world_scale),
                )
                field_patch = prospective if resolved_obj is not None else {**prospective, "type": spec["field_type"]}
                _patch_rna(obj.field, field_patch, _FIELD_FIELDS)
                obj["blendermcp_liquid_force"] = domain_obj.name
            domain.force_collection = collection
            if weights:
                weight_changes = _patch_rna(domain.effector_weights, weights, _WEIGHT_FIELDS)
            bpy.context.view_layer.update()
        except Exception:
            _restore_rna(domain.effector_weights, weight_changes)
            domain.force_collection = old_collection
            for obj, snapshot in reversed(snapshots):
                with contextlib.suppress(Exception):
                    _restore_field(obj, snapshot)
            for obj in linked_objects:
                with contextlib.suppress(Exception):
                    collection.objects.unlink(obj)
            for obj in reversed(created_objects):
                with contextlib.suppress(Exception):
                    bpy.data.objects.remove(obj, do_unlink=True)
            if collection_created_link:
                with contextlib.suppress(Exception):
                    scene.collection.children.unlink(collection)
            raise
        force_info = [
            {
                "object": obj.name,
                "created": obj in created_objects,
                "field": _read_fields(obj.field, _FIELD_FIELDS),
                "coordinate_space": "WORLD",
                "world_location": list(obj.matrix_world.translation),
                "world_rotation_quaternion": list(obj.matrix_world.to_quaternion()),
            }
            for obj, _spec, _prospective in [
                (bpy.data.objects.get(spec["object_name"]), spec, prospective) for _obj, spec, prospective in resolved
            ]
        ]
        return {
            "changed_objects": [domain_obj.name, *[item["object"] for item in force_info]],
            "domain": domain_obj.name,
            "modifier": modifier.name,
            "force_collection": collection.name,
            "force_fields": force_info,
            "effector_weight_changes": weight_changes,
            "scene_gravity_world": list(scene.gravity),
            "domain_gravity_multiplier": float(domain.effector_weights.gravity),
            "invalidated_cache_stages": ["DATA", "MESH", "PARTICLES"],
            "warnings": [
                "Force influence depends on domain resolution and time steps.",
                "Mantaflow does not provide bidirectional rigid-body coupling.",
            ],
        }

    def create_liquid_material(
        self,
        domain_object_name,
        modifier_name,
        material_name,
        config,
        existing_policy="ERROR",
        assignment="APPEND",
        slot_index=None,
    ):
        obj, modifier, settings = _get_domain(domain_object_name, modifier_name)
        if existing_policy not in {"ERROR", "REUSE"}:
            raise ValueError("existing_policy must be ERROR or REUSE")
        if assignment not in {"APPEND", "REPLACE_SLOT"}:
            raise ValueError("assignment must be APPEND or REPLACE_SLOT")
        if assignment == "REPLACE_SLOT":
            if slot_index is None or not 0 <= slot_index < len(obj.data.materials):
                raise ValueError("REPLACE_SLOT requires an existing valid slot_index")
        elif slot_index is not None:
            raise ValueError("slot_index is valid only with REPLACE_SLOT")
        material = bpy.data.materials.get(material_name)
        created = material is None
        if material is not None and existing_policy == "ERROR":
            raise ValueError(f"Material already exists: {material_name}")
        preset = config.get("preset", "WATER")
        if preset not in _MATERIAL_PRESETS:
            raise ValueError(f"Unknown liquid material preset: {preset}")
        values = dict(_MATERIAL_PRESETS[preset])
        values.update({key: value for key, value in config.items() if key != "preset"})
        for color_name in ("base_color", "volume_absorption_color"):
            color = values[color_name]
            _finite(color, color_name)
            if len(color) != 4 or any(not 0.0 <= component <= 1.0 for component in color):
                raise ValueError(f"{color_name} must contain four values in [0, 1]")
        if material is None:
            material = bpy.data.materials.new(material_name)
            nodes = _configure_principled_material(material, values)
            material["blendermcp_liquid_material"] = obj.name
            material["blendermcp_liquid_material_schema"] = 1
        else:
            nodes = None
        old_material = obj.data.materials[slot_index] if assignment == "REPLACE_SLOT" else None
        appended = False
        changed_slot = False
        try:
            if assignment == "APPEND":
                existing_slot = next(
                    (index for index, candidate in enumerate(obj.data.materials) if candidate == material), None
                )
                if existing_slot is None:
                    obj.data.materials.append(material)
                    slot_index = len(obj.data.materials) - 1
                    appended = True
                else:
                    slot_index = existing_slot
            elif old_material != material:
                obj.data.materials[slot_index] = material
                changed_slot = True
            bpy.context.view_layer.update()
        except Exception:
            if appended:
                with contextlib.suppress(Exception):
                    obj.data.materials.pop(index=slot_index)
            elif changed_slot:
                with contextlib.suppress(Exception):
                    obj.data.materials[slot_index] = old_material
            if created:
                with contextlib.suppress(Exception):
                    bpy.data.materials.remove(material)  # pyright: ignore[reportArgumentType]
            raise
        changed_objects = [obj.name] if appended or changed_slot else []
        return {
            "changed_objects": changed_objects,
            "changed_resources": [material.name] if created else [],
            "domain": obj.name,
            "modifier": modifier.name,
            "material": material.name,
            "created": created,
            "configured_nodes": nodes,
            "preset_schema_version": 1,
            "expanded_values": values if created else None,
            "existing_material_reused_unchanged": not created,
            "assignment": {"policy": assignment, "slot_index": slot_index, "slot_changed": bool(changed_objects)},
            "solver_settings_changed": False,
            "cache_changed": False,
            "warnings": [] if settings.use_mesh else ["Liquid mesh generation is currently disabled on this domain."],
        }

    def create_secondary_particle_render_setup(
        self,
        domain_object_name,
        modifier_name,
        representation="OBJECT",
        instance_object_name=None,
        create_instance_sphere=False,
        helper_collection_name="Liquid Particle Helpers",
        helper_object_name="Liquid Particle Instance",
        material_name=None,
        display_percentage=25,
        particle_size=None,
        max_systems=16,
    ):
        obj, modifier, settings = _get_domain(domain_object_name, modifier_name)
        if representation != "OBJECT":
            raise ValueError("Phase 1 supports OBJECT particle representation only")
        if bool(instance_object_name) == bool(create_instance_sphere):
            raise ValueError("Provide exactly one of instance_object_name or create_instance_sphere=True")
        if not settings.has_cache_baked_particles:
            raise ValueError("Secondary particles must be baked before configuring their render systems")
        systems = list(obj.particle_systems)
        if len(systems) > max_systems:
            raise ValueError(f"Domain has {len(systems)} particle systems, exceeding max_systems={max_systems}")
        discovered = [(system, _particle_role(system)) for system in systems]
        eligible = [(system, role) for system, role in discovered if role != "UNKNOWN"]
        if not eligible:
            raise ValueError("No publicly identifiable Mantaflow secondary particle systems were found on the domain")
        scene, _view_layer = _scene_context_for_object(obj)
        material = None
        if material_name:
            material = bpy.data.materials.get(material_name)
            if material is None:
                raise ValueError(f"Material not found: {material_name}")
        created_helper = False
        helper_collection_linked = False
        if instance_object_name:
            instance = _get_object(instance_object_name, {"MESH"})
        else:
            if bpy.data.objects.get(helper_object_name) is not None:
                raise ValueError(f"Helper object already exists: {helper_object_name}")
            collection, _created, helper_collection_linked = _ensure_collection(scene, helper_collection_name)
            mesh = _octahedron_mesh(helper_object_name)
            instance = bpy.data.objects.new(helper_object_name, mesh)
            collection.objects.link(instance)
            instance.scale = (0.025, 0.025, 0.025)
            instance.display_type = "WIRE"
            instance.hide_render = True
            instance["blendermcp_liquid_helper"] = obj.name
            created_helper = True
            if material is not None:
                instance.data.materials.append(material)
        snapshots = []
        try:
            for system, _role in eligible:
                particle_settings = system.settings
                snapshots.append(
                    (
                        particle_settings,
                        particle_settings.render_type,
                        particle_settings.instance_object,
                        particle_settings.display_percentage,
                        particle_settings.particle_size,
                    )
                )
                particle_settings.render_type = "OBJECT"
                particle_settings.instance_object = instance
                particle_settings.display_percentage = display_percentage
                if particle_size is not None:
                    particle_settings.particle_size = particle_size
            bpy.context.view_layer.update()
        except Exception:
            for particle_settings, render_type, old_instance, percentage, old_size in reversed(snapshots):
                with contextlib.suppress(Exception):
                    particle_settings.render_type = render_type
                    particle_settings.instance_object = old_instance
                    particle_settings.display_percentage = percentage
                    particle_settings.particle_size = old_size
            if created_helper:
                with contextlib.suppress(Exception):
                    bpy.data.objects.remove(instance, do_unlink=True)  # pyright: ignore[reportArgumentType]
            if helper_collection_linked:
                with contextlib.suppress(Exception):
                    scene.collection.children.unlink(collection)  # pyright: ignore[reportArgumentType]
            raise
        mappings = []
        for system, role in discovered:
            count = len(system.particles)
            mappings.append(
                {
                    "system": system.name,
                    "settings": system.settings.name,
                    "role": role,
                    "configured": role != "UNKNOWN",
                    "particle_count_current_frame": count,
                    "estimated_viewport_instances": math.ceil(count * display_percentage / 100),
                    "estimated_render_instances": count,
                }
            )
        return {
            "changed_objects": [obj.name, instance.name] if created_helper else [obj.name],
            "domain": obj.name,
            "modifier": modifier.name,
            "instance_object": instance.name,
            "instance_helper_created": created_helper,
            "systems": mappings,
            "unknown_systems_left_unchanged": [item["system"] for item in mappings if not item["configured"]],
            "classification_basis": "Public particle-system and settings labels; unrecognized systems are not mutated.",
            "warnings": ["Particle counts are observed at the current frame and may vary over the bake."],
        }

    def sample_liquid_simulation(
        self,
        domain_object_name,
        modifier_name,
        frames,
        timeout_seconds=30.0,
        boundary_tolerance_cells=1.0,
    ):
        obj, modifier, settings = _get_domain(domain_object_name, modifier_name)
        if not frames or len(frames) > 32 or len(set(frames)) != len(frames):
            raise ValueError("frames must contain 1-32 unique frame numbers")
        normalized = sorted(int(frame) for frame in frames)
        if settings.cache_type != "REPLAY" and not settings.has_cache_baked_any:
            raise ValueError("Sampling requires REPLAY cache mode or an existing modular/final bake")
        scene, view_layer = _scene_context_for_object(obj)
        if any(frame < scene.frame_start or frame > scene.frame_end for frame in normalized):
            raise ValueError("All sample frames must be inside the scene frame range")
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        deadline = time.monotonic() + timeout_seconds
        domain_bounds = _world_bounds(obj, evaluated=False)
        cell = max(domain_bounds["dimensions"]) / settings.resolution_max
        tolerance = cell * boundary_tolerance_cells
        samples = []
        timed_out = False
        try:
            for frame in normalized:
                scene.frame_set(frame)
                view_layer.update()
                output = _evaluated_output(obj)
                particle_counts = {system.name: len(system.particles) for system in obj.particle_systems}
                output_bounds = output["bounds"]
                near_faces = []
                if output_bounds:
                    faces = (
                        ("LEFT", 0, "minimum"),
                        ("RIGHT", 0, "maximum"),
                        ("BACK", 1, "minimum"),
                        ("FRONT", 1, "maximum"),
                        ("BOTTOM", 2, "minimum"),
                        ("TOP", 2, "maximum"),
                    )
                    for label, axis, side in faces:
                        if abs(output_bounds[side][axis] - domain_bounds[side][axis]) <= tolerance:
                            near_faces.append(label)
                samples.append(
                    {
                        "frame": frame,
                        "evaluated_mesh": output,
                        "particle_counts": particle_counts,
                        "total_particles": sum(particle_counts.values()),
                        "empty_output": output["vertices"] == 0 and not any(particle_counts.values()),
                        "near_domain_faces": near_faces,
                    }
                )
                if time.monotonic() >= deadline and frame != normalized[-1]:
                    timed_out = True
                    break
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
            view_layer.update()
        discontinuities = []
        for previous, current in itertools.pairwise(samples):
            before = previous["evaluated_mesh"]["vertices"]
            after = current["evaluated_mesh"]["vertices"]
            ratio = abs(after - before) / max(before, 1)
            if ratio > 0.75:
                discontinuities.append(
                    {"from_frame": previous["frame"], "to_frame": current["frame"], "vertex_count_change_ratio": ratio}
                )
        return {
            "changed_objects": [obj.name] if settings.cache_type == "REPLAY" else [],
            "domain": obj.name,
            "modifier": modifier.name,
            "cache_type": settings.cache_type,
            "requested_frames": normalized,
            "evaluated_frames": [item["frame"] for item in samples],
            "timed_out": timed_out,
            "timeout_seconds": timeout_seconds,
            "domain_bounds": domain_bounds,
            "estimated_cell_size": cell,
            "samples": samples,
            "large_frame_changes": discontinuities,
            "timeline_restored": {"frame": scene.frame_current, "subframe": scene.frame_subframe},
            "cache_effect": (
                "Frame evaluation may populate the REPLAY cache. Existing modular/final cache files are not changed."
            ),
            "claim": "Bounded numerical evidence only; this is not a final bake or visual-quality assessment.",
        }

    def manage_liquid_cache(
        self,
        domain_object_name,
        modifier_name,
        action="STATUS",
        patch=None,
        confirm_bake=False,
        confirm_free=False,
        confirm_external_path=False,
        confirm_external_overwrite=False,
        max_bake_frames=250,
        max_existing_cache_bytes=10_000_000_000,
    ):
        obj, modifier, settings = _get_domain(domain_object_name, modifier_name)
        actions = {
            "STATUS",
            "CONFIGURE",
            "BAKE_DATA",
            "BAKE_GUIDES",
            "BAKE_MESH",
            "BAKE_PARTICLES",
            "BAKE_ALL",
            "PAUSE",
            "FREE_DATA",
            "FREE_GUIDES",
            "FREE_MESH",
            "FREE_PARTICLES",
            "FREE_ALL",
        }
        if action not in actions:
            raise ValueError(f"Unsupported liquid cache action: {action}")
        patch = dict(patch or {})
        if action == "CONFIGURE" and not patch:
            raise ValueError("CONFIGURE requires a nonempty cache patch")
        if action != "CONFIGURE" and patch:
            raise ValueError(f"{action} does not accept a cache patch")
        before = _cache_state(settings)
        path_before = _cache_directory_evidence(settings.cache_directory)
        if action == "STATUS":
            return {
                "changed_objects": [],
                "domain": obj.name,
                "modifier": modifier.name,
                "action": action,
                "cache": before,
                "directory": path_before,
            }
        if action == "CONFIGURE":
            _reject_cache_flags(settings, _CACHE_FLAGS, "Cannot configure an active or baked cache")
            unknown = set(patch) - _CACHE_CONFIG_FIELDS
            if unknown:
                raise ValueError(f"Unsupported cache properties: {sorted(unknown)}")
            start = patch.get("cache_frame_start", settings.cache_frame_start)
            end = patch.get("cache_frame_end", settings.cache_frame_end)
            if start > end:
                raise ValueError("cache_frame_start must be <= cache_frame_end")
            if "cache_directory" in patch:
                if not confirm_external_path:
                    raise ValueError("Changing cache_directory requires confirm_external_path=True")
                resolved = _resolved_cache_path(patch["cache_directory"])
                if not os.path.isdir(resolved) or not os.access(resolved, os.W_OK):
                    raise ValueError(f"Cache directory must already exist and be writable: {resolved}")
                for other in bpy.data.objects:
                    for other_modifier in other.modifiers:
                        if other_modifier == modifier or other_modifier.type != "FLUID":
                            continue
                        other_settings = getattr(other_modifier, "domain_settings", None)
                        if other_settings and _resolved_cache_path(other_settings.cache_directory) == resolved:
                            raise ValueError(f"Cache directory is already used by '{other.name}:{other_modifier.name}'")
            if patch.get("cache_type") == "FINAL":
                patch["cache_type"] = "ALL"
            old_range = (settings.cache_frame_start, settings.cache_frame_end)
            scalar_patch = {
                name: value for name, value in patch.items() if name not in {"cache_frame_start", "cache_frame_end"}
            }
            changes = _patch_rna(settings, scalar_patch, _CACHE_CONFIG_FIELDS)
            try:
                if "cache_frame_start" in patch or "cache_frame_end" in patch:
                    _set_cache_range(settings, start, end)
                    changes["cache_frame_start"] = {"old": old_range[0], "new": settings.cache_frame_start}
                    changes["cache_frame_end"] = {"old": old_range[1], "new": settings.cache_frame_end}
                _update_or_restore(obj, settings, changes)
            except Exception:
                _restore_rna(settings, changes)
                with contextlib.suppress(Exception):
                    _set_cache_range(settings, *old_range)
                raise
            return {
                "changed_objects": [obj.name],
                "domain": obj.name,
                "modifier": modifier.name,
                "action": action,
                "changes": changes,
                "cache_before": before,
                "cache_after": _cache_state(settings),
                "directory": _cache_directory_evidence(settings.cache_directory),
                "warnings": ["Cache configuration changed; any in-memory replay state is stale."],
            }
        frame_count = settings.cache_frame_end - settings.cache_frame_start + 1
        bake_actions = {"BAKE_DATA", "BAKE_GUIDES", "BAKE_MESH", "BAKE_PARTICLES", "BAKE_ALL"}
        free_actions = {"FREE_DATA", "FREE_GUIDES", "FREE_MESH", "FREE_PARTICLES", "FREE_ALL"}
        if action in bake_actions:
            if not confirm_bake:
                raise ValueError(f"{action} requires confirm_bake=True")
            if frame_count > max_bake_frames:
                raise ValueError(f"Cache range has {frame_count} frames, exceeding max_bake_frames={max_bake_frames}")
            if settings.cache_type == "REPLAY":
                raise ValueError("Explicit baking is unavailable in REPLAY mode; configure MODULAR or ALL first")
            if action == "BAKE_ALL" and settings.cache_type != "ALL":
                raise ValueError("BAKE_ALL requires cache_type ALL")
            if action != "BAKE_ALL" and settings.cache_type != "MODULAR":
                raise ValueError(f"{action} requires cache_type MODULAR")
            if action in {"BAKE_MESH", "BAKE_PARTICLES"} and not settings.has_cache_baked_data:
                raise ValueError(f"{action} requires the DATA stage to be baked first")
            if action == "BAKE_MESH" and not settings.use_mesh:
                raise ValueError("BAKE_MESH requires use_mesh=True")
            if action == "BAKE_PARTICLES" and not any(getattr(settings, name) for name in _SECONDARY_TOGGLES):
                raise ValueError("BAKE_PARTICLES requires at least one enabled secondary particle type")
            directory = _cache_directory_evidence(settings.cache_directory)
            if not directory["exists"] or not directory["writable"]:
                raise ValueError(f"Configured cache directory must exist and be writable: {directory['resolved']}")
            if directory["scan_truncated"] or directory["bytes_scanned"] > max_existing_cache_bytes:
                raise ValueError("Existing cache directory exceeds the configured inspection bound")
            if directory["files_scanned"] and not confirm_external_overwrite:
                raise ValueError("Cache directory is not empty; confirm_external_overwrite=True is required")
        if action in free_actions:
            if not confirm_free:
                raise ValueError(f"{action} requires confirm_free=True")
            if path_before["files_scanned"] and not confirm_external_overwrite:
                raise ValueError("Freeing cache data may remove files; confirm_external_overwrite=True is required")
        operator = {
            "BAKE_DATA": bpy.ops.fluid.bake_data,
            "BAKE_GUIDES": bpy.ops.fluid.bake_guides,
            "BAKE_MESH": bpy.ops.fluid.bake_mesh,
            "BAKE_PARTICLES": bpy.ops.fluid.bake_particles,
            "BAKE_ALL": bpy.ops.fluid.bake_all,
            "PAUSE": bpy.ops.fluid.pause_bake,
            "FREE_DATA": bpy.ops.fluid.free_data,
            "FREE_GUIDES": bpy.ops.fluid.free_guides,
            "FREE_MESH": bpy.ops.fluid.free_mesh,
            "FREE_PARTICLES": bpy.ops.fluid.free_particles,
            "FREE_ALL": bpy.ops.fluid.free_all,
        }[action]
        if action == "PAUSE" and not settings.is_cache_baking_any:
            raise ValueError("No liquid cache stage is currently baking")
        expected_before = {
            "FREE_DATA": "has_cache_baked_data",
            "FREE_GUIDES": "has_cache_baked_guide",
            "FREE_MESH": "has_cache_baked_mesh",
            "FREE_PARTICLES": "has_cache_baked_particles",
        }.get(action)
        if expected_before and not getattr(settings, expected_before):
            raise ValueError(f"{action} has no baked stage to free")
        _run_fluid_operator(obj, operator)
        after = _cache_state(settings)
        expected_after = {
            "BAKE_DATA": ("has_cache_baked_data", True),
            "BAKE_GUIDES": ("has_cache_baked_guide", True),
            "BAKE_MESH": ("has_cache_baked_mesh", True),
            "BAKE_PARTICLES": ("has_cache_baked_particles", True),
            "BAKE_ALL": ("has_cache_baked_any", True),
            "FREE_DATA": ("has_cache_baked_data", False),
            "FREE_GUIDES": ("has_cache_baked_guide", False),
            "FREE_MESH": ("has_cache_baked_mesh", False),
            "FREE_PARTICLES": ("has_cache_baked_particles", False),
            "FREE_ALL": ("has_cache_baked_any", False),
        }.get(action)
        if expected_after and bool(getattr(settings, expected_after[0])) != expected_after[1]:
            raise RuntimeError(
                f"{action} reported FINISHED but {expected_after[0]} is not {expected_after[1]}; "
                f"state={json.dumps(after)}"
            )
        return {
            "changed_objects": [obj.name],
            "domain": obj.name,
            "modifier": modifier.name,
            "action": action,
            "frame_count": frame_count,
            "operator_scope": "EXACT_LIQUID_DOMAIN",
            "cache_before": before,
            "cache_after": after,
            "directory_before": path_before,
            "directory_after": _cache_directory_evidence(settings.cache_directory),
            "warnings": [
                "Fluid bake operators are Blender jobs; frame count is bounded but a single frame cannot be "
                "timed out by MCP.",
                "Free actions delete derived cache data and cannot be rolled back through Blender datablocks.",
            ],
        }

    def remove_fluid_components(self, targets, accept_orphaned_cache=False):
        if not targets or len(targets) > 64:
            raise ValueError("targets must contain 1-64 records")
        identities = [(item["object_name"], item["modifier_name"]) for item in targets]
        if len(set(identities)) != len(identities):
            raise ValueError("targets contain duplicate object/modifier pairs")
        resolved = []
        helper_names = set()
        for record in targets:
            obj = _get_object(record["object_name"])
            modifier = obj.modifiers.get(record["modifier_name"])
            if modifier is None or modifier.type != "FLUID" or modifier.fluid_type == "NONE":
                raise ValueError(f"Active fluid modifier not found: {obj.name}:{record['modifier_name']}")
            cache = None
            if modifier.fluid_type == "DOMAIN":
                settings = modifier.domain_settings
                if settings is None:
                    raise ValueError(f"Domain settings are unavailable: {obj.name}:{modifier.name}")
                active = _active_cache_flags(settings)
                if active and not accept_orphaned_cache:
                    raise ValueError(
                        f"Domain '{obj.name}:{modifier.name}' has baked/baking cache state {active}; free it first "
                        "or explicitly accept orphaning"
                    )
                cache = {
                    "state": _cache_state(settings),
                    "directory": _cache_directory_evidence(settings.cache_directory),
                }
                if cache["directory"]["files_scanned"] and not accept_orphaned_cache:
                    raise ValueError(
                        f"Domain '{obj.name}:{modifier.name}' has files in its cache directory; free the cache "
                        "first or explicitly accept orphaning"
                    )
            if record.get("remove_owned_helper_object"):
                if obj.get("blendermcp_liquid_helper") is None:
                    raise ValueError(f"Object '{obj.name}' is not tagged as an MCP-owned liquid helper")
                if len(obj.modifiers) != 1:
                    raise ValueError("Owned helper removal requires the fluid modifier to be its only modifier")
                helper_names.add(obj.name)
            resolved.append((obj, modifier, record, cache))
        removed = []
        for obj, modifier, record, cache in resolved:
            info = {
                "object": obj.name,
                "modifier": modifier.name,
                "fluid_type": modifier.fluid_type,
                "cache_removed_with_modifier": cache,
                "external_cache_files_deleted": False,
                "helper_object_removed": bool(record.get("remove_owned_helper_object")),
            }
            obj.modifiers.remove(modifier)
            removed.append(info)
        for name in helper_names:
            helper = bpy.data.objects.get(name)
            if helper is not None:
                bpy.data.objects.remove(helper, do_unlink=True)  # pyright: ignore[reportArgumentType]
        changed = [record["object"] for record in removed]
        return {
            "changed_objects": changed,
            "removed": removed,
            "retained": [
                "non-helper source/render objects",
                "mesh datablocks",
                "materials",
                "other modifiers",
                "collections",
                "external cache directories and files",
            ],
            "recovery": "Use Blender Undo for modifier/helper recovery; external cache files were not touched.",
            "warnings": (
                ["One or more domain modifiers were removed while cache paths/files may remain orphaned."]
                if accept_orphaned_cache and any(item["cache_removed_with_modifier"] for item in removed)
                else []
            ),
        }
