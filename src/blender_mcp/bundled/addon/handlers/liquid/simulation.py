# pyright: reportArgumentType=false, reportGeneralTypeIssues=false
"""Bounded liquid-domain evaluation and Mantaflow cache lifecycle handling."""

import contextlib
import itertools
import json
import math
import os
import time

import bpy

from ...helpers import preserve_mode_and_selection, set_active
from .inspection_and_setup import (
    _CACHE_FIELDS,
    _CACHE_FLAGS,
    _get_domain,
    _patch_rna,
    _read_fields,
    _resolved_cache_path,
    _restore_rna,
    _validate_rna_value,
    _world_bounds,
)

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


class LiquidSimulationHandlers:
    """Sample evaluated liquid motion and manage only the requested domain's cache."""

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
