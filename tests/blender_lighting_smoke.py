"""Run with Blender 5.1+ to smoke-test production lighting data and world operations."""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import tempfile

from pathlib import Path

import bpy
import mathutils

addon_path = Path(__file__).resolve().parents[1] / "src" / "blender_mcp" / "bundled" / "addon" / "__init__.py"
package_name = "blender_mcp_lighting_smoke"
spec = importlib.util.spec_from_file_location(
    package_name, addon_path, submodule_search_locations=[str(addon_path.parent)]
)
assert spec is not None
addon = importlib.util.module_from_spec(spec)
sys.modules[package_name] = addon
spec.loader.exec_module(addon)

from blender_mcp_lighting_smoke.handlers.lighting import LightingHandlers  # noqa: E402


def main() -> None:
    """Exercise representative light, aiming, linking, world, color, and inspection workflows."""
    handler = LightingHandlers()
    scene = bpy.context.scene
    scene.name = "Lighting Smoke"
    receivers = bpy.data.collections.new("Lighting Receivers")
    scene.collection.children.link(receivers)

    created = handler.create_light(
        scene.name,
        "Lighting",
        "Key Light",
        "AREA",
        (4.0, -4.0, 5.0),
        (0.0, 0.0, 0.0),
        {"energy": 800.0, "shape": "RECTANGLE", "size": 3.0, "size_y": 2.0},
    )
    assert created["object"] == "Key Light"
    assert created["settings"]["shape"] == "RECTANGLE"

    configured = handler.configure_light("Key Light", {"exposure": 1.0, "use_shadow": True})
    assert configured["new"]["exposure"] == 1.0

    aimed = handler.aim_light(scene.name, "Key Light", target_point=(0.0, 0.0, 0.0))
    assert aimed["method"] == "STATIC_ROTATION"
    direction = bpy.data.objects["Key Light"].matrix_world.to_quaternion() @ mathutils.Vector((0, 0, -1))
    expected = (mathutils.Vector((0, 0, 0)) - bpy.data.objects["Key Light"].matrix_world.translation).normalized()
    assert sum(left * right for left, right in zip(direction, expected, strict=True)) > 0.99999

    linking = handler.configure_light_linking(scene.name, "Key Light", "Lighting Receivers")
    assert linking["after"]["receiver"]["collection"] == "Lighting Receivers"

    world = handler.configure_world_background(scene.name, (0.04, 0.05, 0.08), 0.7, False, "Lighting World", True)
    assert world["source"] == "BACKGROUND"

    sky = handler.configure_procedural_sky(
        scene.name,
        {
            "sky_type": "MULTIPLE_SCATTERING",
            "sun_elevation": math.radians(35),
            "sun_rotation": math.radians(110),
            "sun_size": math.radians(0.53),
            "background_strength": 0.8,
        },
        "BOTH",
        True,
        "Sky Sun",
        "Lighting",
        2.0,
    )
    assert sky["synchronized_sun"] == "Sky Sun"

    with tempfile.TemporaryDirectory(prefix="blender_mcp_lighting_") as temp_directory:
        environment_path = os.path.join(temp_directory, "smoke_environment.hdr")
        source_image = bpy.data.images.new("Smoke Environment Source", width=4, height=2, float_buffer=True)
        source_image.generated_color = (0.2, 0.3, 0.5, 1.0)
        source_image.filepath_raw = environment_path
        source_image.file_format = "HDR"
        source_image.save()
        bpy.data.images.remove(source_image)
        hdri = handler.configure_hdri_environment(scene.name, environment_path, strength=0.5, rotation=0.25)
        assert hdri["source"] == "HDRI"
        assert hdri["projection"] == "EQUIRECTANGULAR"

        quality = handler.configure_lighting_quality(
            scene.name,
            "EEVEE",
            eevee={"render_samples": 4, "shadow_ray_count": 1, "shadow_step_count": 4},
        )
        assert quality["expanded_values"]["eevee"]["render_samples"] == 4

        original_resolution = (scene.render.resolution_x, scene.render.resolution_y)
        preview_path = os.path.join(temp_directory, "lighting_preview.png")
        preview = handler.render_lighting_preview(
            scene.name,
            scene.camera.name,
            scene.frame_current,
            "EEVEE",
            32,
            32,
            1,
            {"EEVEE": preview_path},
        )
        assert preview["outputs"][0]["size_bytes"] > 0
        assert preview["warnings"] == []
        assert (scene.render.resolution_x, scene.render.resolution_y) == original_resolution

    view = bpy.context.scene.view_settings.view_transform
    color = handler.configure_color_management(scene.name, view_transform=view, exposure=0.0)
    assert color["after"]["view_transform"] == view

    inventory = handler.list_lights(scene.name)
    assert inventory["total"] >= 2
    assert {"Key Light", "Sky Sun"}.issubset({item["object"] for item in inventory["lights"]})
    inspected = handler.inspect_lighting_setup(scene.name)
    assert inspected["world"]["name"] == "Lighting World"
    validated = handler.validate_lighting_setup(scene.name, "EEVEE")
    assert "findings" in validated

    print("LIGHTING_SMOKE_OK")


if __name__ == "__main__":
    main()
