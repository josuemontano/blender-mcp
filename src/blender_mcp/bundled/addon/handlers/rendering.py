# ruff: file-ignore[missing-return-type-private-function, missing-return-type-undocumented-public-function, missing-type-function-argument, no-self-use, too-many-arguments, too-many-branches, too-many-positional-arguments, too-many-statements-in-try-clause, undocumented-public-method]
"""Blender-side scene rendering and view-layer handlers."""

import math
import os

from contextlib import suppress

import bpy

_VIEW_LAYER_PROPERTIES = {
    "use",
    "use_sky",
    "use_solid",
    "use_strand",
    "use_pass_combined",
    "use_pass_z",
    "use_pass_mist",
    "use_pass_normal",
    "use_pass_position",
    "use_pass_vector",
    "use_pass_uv",
    "use_pass_object_index",
    "use_pass_material_index",
    "use_pass_cryptomatte_object",
    "use_pass_cryptomatte_material",
    "use_pass_cryptomatte_asset",
    "pass_cryptomatte_depth",
}

_RENDER_PROPERTIES = {
    "engine",
    "resolution_x",
    "resolution_y",
    "resolution_percentage",
    "pixel_aspect_x",
    "pixel_aspect_y",
    "fps",
    "fps_base",
    "film_transparent",
}
_SCENE_PROPERTIES = {"frame_start", "frame_end", "frame_step"}
_IMAGE_PROPERTY_MAPPING = {
    "image_format": "file_format",
    "color_mode": "color_mode",
    "color_depth": "color_depth",
    "compression": "compression",
    "quality": "quality",
}
_CYCLES_PROPERTY_MAPPING = {"cycles_samples": "samples", "cycles_use_denoising": "use_denoising"}
_RENDER_PATCH_PROPERTIES = (
    _RENDER_PROPERTIES | _SCENE_PROPERTIES | set(_IMAGE_PROPERTY_MAPPING) | set(_CYCLES_PROPERTY_MAPPING)
)
_MAX_ANIMATION_FRAMES = 10_000


def _scene(name):
    scene = bpy.data.scenes.get(name) if name else bpy.context.scene
    if scene is None:
        raise ValueError(f"Scene not found: {name}")
    return scene


def _pass_info(layer):
    return {
        name: getattr(layer, name)
        for name in sorted(_VIEW_LAYER_PROPERTIES)
        if name.startswith("use_pass_") or name == "pass_cryptomatte_depth"
    }


def _layer_info(layer):
    return {
        "name": layer.name,
        "use": layer.use,
        "use_sky": layer.use_sky,
        "use_solid": layer.use_solid,
        "use_strand": layer.use_strand,
        "material_override": layer.material_override.name if layer.material_override else None,
        "world_override": layer.world_override.name if layer.world_override else None,
        "passes": _pass_info(layer),
    }


def _render_info(scene):
    render = scene.render
    image = render.image_settings
    cycles = getattr(scene, "cycles", None)
    compositor_tree = getattr(scene, "compositing_node_group", None) or getattr(scene, "node_tree", None)
    return {
        "scene": scene.name,
        "engine": render.engine,
        "camera": scene.camera.name if scene.camera else None,
        "resolution": [render.resolution_x, render.resolution_y, render.resolution_percentage],
        "pixel_aspect": [render.pixel_aspect_x, render.pixel_aspect_y],
        "fps": render.fps,
        "fps_base": render.fps_base,
        "frame_range": [scene.frame_start, scene.frame_end, scene.frame_step],
        "film_transparent": render.film_transparent,
        "output": {
            "filepath": render.filepath,
            "file_format": image.file_format,
            "color_mode": image.color_mode,
            "color_depth": image.color_depth,
            "compression": image.compression,
            "quality": image.quality,
            "use_file_extension": render.use_file_extension,
        },
        "cycles": {
            "samples": cycles.samples,
            "use_denoising": cycles.use_denoising,
        }
        if cycles is not None
        else None,
        "view_layers": [_layer_info(layer) for layer in scene.view_layers],
        "compositor": {
            "use_nodes": scene.use_nodes,
            "node_tree": compositor_tree.name if compositor_tree else None,
            "node_count": len(compositor_tree.nodes) if compositor_tree else 0,
        },
    }


def _set_properties(owner, patch, mapping=None):
    previous = {}
    mapping = mapping or {}
    try:
        for name, value in patch.items():
            property_name = mapping.get(name, name)
            previous[property_name] = getattr(owner, property_name)
            setattr(owner, property_name, value)
    except Exception:
        for name, value in previous.items():
            with suppress(Exception):
                setattr(owner, name, value)
        raise
    return previous


def _validate_render_patch(patch):
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch must be a non-empty object")
    unknown = sorted(set(patch) - _RENDER_PATCH_PROPERTIES)
    if unknown:
        raise ValueError(f"Unsupported render settings: {unknown}")
    numeric_ranges = {
        "resolution_x": (4, 65_536),
        "resolution_y": (4, 65_536),
        "resolution_percentage": (1, 100),
        "pixel_aspect_x": (0, 200),
        "pixel_aspect_y": (0, 200),
        "fps": (1, 960),
        "fps_base": (0, 1000),
        "frame_step": (1, None),
        "compression": (0, 100),
        "quality": (0, 100),
        "cycles_samples": (1, 16_384),
    }
    for name, (minimum, maximum) in numeric_ranges.items():
        if name not in patch:
            continue
        value = patch[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        if value < minimum or (
            minimum == 0 and name in {"pixel_aspect_x", "pixel_aspect_y", "fps_base"} and value == 0
        ):
            comparator = "greater than" if minimum == 0 else "at least"
            raise ValueError(f"{name} must be {comparator} {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{name} must be at most {maximum}")
    allowed_values = {
        "engine": {"BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH", "CYCLES"},
        "image_format": {"PNG", "JPEG", "OPEN_EXR", "TIFF", "WEBP"},
        "color_mode": {"BW", "RGB", "RGBA"},
        "color_depth": {"8", "16", "32"},
    }
    for name, allowed in allowed_values.items():
        if name in patch and patch[name] not in allowed:
            raise ValueError(f"Unsupported {name}: {patch[name]}")
    return patch


class RenderingHandlersMixin:
    """Expose production render configuration and bounded rendering."""

    def inspect_render_setup(self, scene_name=None):
        return _render_info(_scene(scene_name))

    def configure_render_settings(self, scene_name, patch):
        scene = _scene(scene_name)
        patch = _validate_render_patch(patch)
        resulting_start = patch.get("frame_start", scene.frame_start)
        resulting_end = patch.get("frame_end", scene.frame_end)
        if resulting_end < resulting_start:
            raise ValueError("Resulting frame_end must be greater than or equal to frame_start")
        snapshots = []
        try:
            snapshots.append(
                (
                    scene.render,
                    _set_properties(scene.render, {k: v for k, v in patch.items() if k in _RENDER_PROPERTIES}),
                )
            )
            snapshots.append(
                (scene, _set_properties(scene, {k: v for k, v in patch.items() if k in _SCENE_PROPERTIES}))
            )
            snapshots.append(
                (
                    scene.render.image_settings,
                    _set_properties(
                        scene.render.image_settings,
                        {k: v for k, v in patch.items() if k in _IMAGE_PROPERTY_MAPPING},
                        _IMAGE_PROPERTY_MAPPING,
                    ),
                )
            )
            cycles_patch = {k: v for k, v in patch.items() if k in _CYCLES_PROPERTY_MAPPING}
            if cycles_patch:
                if not hasattr(scene, "cycles"):
                    raise ValueError("Cycles settings are unavailable in this Blender build")
                snapshots.append((scene.cycles, _set_properties(scene.cycles, cycles_patch, _CYCLES_PROPERTY_MAPPING)))
            if scene.frame_end < scene.frame_start:
                raise ValueError("Resulting frame_end must be greater than or equal to frame_start")
        except Exception:
            for owner, values in reversed(snapshots):
                for name, value in values.items():
                    with suppress(Exception):
                        setattr(owner, name, value)
            raise
        return {"changed": sorted(patch), "settings": _render_info(scene), "changed_resources": [scene.name]}

    def manage_view_layers(self, scene_name, action, view_layer_name, patch=None, confirm_remove=False):
        scene = _scene(scene_name)
        action = str(action).upper()
        if action not in {"CREATE", "PATCH", "REMOVE"}:
            raise ValueError(f"Unsupported view-layer action: {action}")
        if not isinstance(view_layer_name, str) or not view_layer_name.strip():
            raise ValueError("view_layer_name must be a non-empty string")
        if patch is not None and not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        if action == "PATCH" and not patch:
            raise ValueError("PATCH requires a non-empty patch")
        if action == "REMOVE" and patch:
            raise ValueError("REMOVE does not accept patch")
        layer = scene.view_layers.get(view_layer_name)
        if action == "CREATE":
            if layer is not None:
                raise ValueError(f"View layer already exists: {view_layer_name}")
            layer = scene.view_layers.new(view_layer_name)
        elif layer is None:
            raise ValueError(f"View layer not found: {view_layer_name}")
        if action in {"CREATE", "PATCH"}:
            patch = patch or {}
            unknown = sorted(set(patch) - _VIEW_LAYER_PROPERTIES - {"material_override", "world_override"})
            if unknown:
                raise ValueError(f"Unsupported view-layer settings: {unknown}")
            prepared = {key: value for key, value in patch.items() if key in _VIEW_LAYER_PROPERTIES}
            if "material_override" in patch:
                name = patch["material_override"]
                material = bpy.data.materials.get(name) if name else None
                if name and material is None:
                    raise ValueError(f"Material not found: {name}")
                prepared["material_override"] = material
            if "world_override" in patch:
                name = patch["world_override"]
                world = bpy.data.worlds.get(name) if name else None
                if name and world is None:
                    raise ValueError(f"World not found: {name}")
                prepared["world_override"] = world
            try:
                _set_properties(layer, prepared)
            except Exception:
                if action == "CREATE":
                    scene.view_layers.remove(layer)
                raise
        elif action == "REMOVE":
            if not confirm_remove:
                raise ValueError("confirm_remove=True is required")
            if len(scene.view_layers) == 1:
                raise ValueError("A scene must retain at least one view layer")
            scene.view_layers.remove(layer)
            return {"removed": view_layer_name, "changed_resources": [view_layer_name]}
        return {"view_layer": _layer_info(layer), "changed_resources": [layer.name]}

    def render_scene(
        self,
        scene_name,
        filepath,
        mode="STILL",
        view_layer_name=None,
        frame=None,
        max_animation_frames=250,
        confirm_render=False,
        confirm_overwrite=False,
    ):
        if not confirm_render:
            raise ValueError("confirm_render=True is required")
        scene = _scene(scene_name)
        mode = str(mode).upper()
        if mode not in {"STILL", "ANIMATION"}:
            raise ValueError("mode must be STILL or ANIMATION")
        if isinstance(max_animation_frames, bool) or not isinstance(max_animation_frames, int):
            raise ValueError("max_animation_frames must be an integer")
        if not 1 <= max_animation_frames <= _MAX_ANIMATION_FRAMES:
            raise ValueError("max_animation_frames must be between 1 and 10000")
        if frame is not None and (isinstance(frame, bool) or not isinstance(frame, int)):
            raise ValueError("frame must be an integer")
        if mode == "ANIMATION" and frame is not None:
            raise ValueError("frame is only valid for STILL renders")
        if not isinstance(filepath, str) or not filepath.strip():
            raise ValueError("filepath must be a non-empty string")
        output = os.path.abspath(bpy.path.abspath(filepath))
        directory = os.path.dirname(output)
        if not directory or not os.path.isdir(directory):
            raise ValueError(f"Output directory does not exist: {directory}")
        if mode == "STILL" and os.path.exists(output) and not confirm_overwrite:
            raise ValueError("Output file already exists; set confirm_overwrite=True to replace it")
        if view_layer_name and scene.view_layers.get(view_layer_name) is None:
            raise ValueError(f"View layer not found: {view_layer_name}")
        frame_count = ((scene.frame_end - scene.frame_start) // scene.frame_step) + 1
        if mode == "ANIMATION" and frame_count > max_animation_frames:
            raise ValueError(
                f"Animation contains {frame_count} frames, exceeding max_animation_frames={max_animation_frames}"
            )

        original_path = scene.render.filepath
        original_frame = scene.frame_current
        try:
            scene.render.filepath = output
            if frame is not None:
                scene.frame_set(frame)
            kwargs = {"animation": mode == "ANIMATION", "write_still": mode == "STILL", "scene": scene.name}
            if view_layer_name:
                kwargs["layer"] = view_layer_name
            result = bpy.ops.render.render(**kwargs)
            if "FINISHED" not in result:
                raise RuntimeError(f"Blender render was cancelled: {result}")
        finally:
            scene.render.filepath = original_path
            scene.frame_set(original_frame)
        return {
            "scene": scene.name,
            "mode": mode,
            "filepath": output,
            "frame": frame if frame is not None else scene.frame_current,
            "frame_count": frame_count if mode == "ANIMATION" else 1,
            "operator_result": sorted(result),
            "settings_restored": True,
        }
