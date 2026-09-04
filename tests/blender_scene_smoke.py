"""Run with Blender 5.1+ to smoke-test declarative scene composition handlers."""

import importlib.util
import sys
import tempfile

from pathlib import Path

import bpy

addon_path = Path(__file__).resolve().parents[1] / "src" / "blender_mcp" / "bundled" / "addon" / "__init__.py"
package_name = "blender_mcp_scene_smoke"
spec = importlib.util.spec_from_file_location(
    package_name,
    addon_path,
    submodule_search_locations=[str(addon_path.parent)],
)
assert spec is not None
addon = importlib.util.module_from_spec(spec)
sys.modules[package_name] = addon
spec.loader.exec_module(addon)

from blender_mcp_scene_smoke.handlers.scene import SceneHandlersMixin  # ruff: ignore[module-import-not-at-top-of-file]


def main() -> None:
    """Exercise native geometry, transforms, collections, hierarchy, constraints, and modifiers."""
    handler = SceneHandlersMixin()
    triangle = handler.create_geometry_object(
        "Scene Smoke Triangle",
        {
            "kind": "MESH",
            "vertices": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            "edges": [],
            "faces": [(0, 1, 2)],
        },
    )
    assert triangle["type"] == "MESH"

    curve = handler.create_geometry_object(
        "Scene Smoke Curve",
        {
            "kind": "CURVE",
            "splines": [{"type": "BEZIER", "points": [(0, 0, 0), (1, 1, 0)]}],
            "bevel_depth": 0.05,
        },
    )
    assert curve["type"] == "CURVE"

    points = handler.create_geometry_object(
        "Scene Smoke Points",
        {"kind": "POINTCLOUD", "points": [(0, 0, 0), (1, 2, 3)], "radii": [0.1, 0.2]},
    )
    assert points["type"] == "POINTCLOUD"

    volume_file = Path(tempfile.gettempdir()) / "blender_mcp_missing_smoke.vdb"
    try:
        handler.create_geometry_object("Missing Volume", {"kind": "VOLUME", "filepath": str(volume_file)})
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("Missing volume path was accepted")

    handler.set_object_transform(
        "Scene Smoke Triangle",
        {"location": (2, 3, 4), "rotation_quaternion": (1, 0, 0, 0), "scale": (2, 2, 2)},
        "WORLD",
    )
    obj = bpy.data.objects["Scene Smoke Triangle"]
    assert tuple(round(value, 5) for value in obj.matrix_world.translation) == (2.0, 3.0, 4.0)

    copies = handler.duplicate_or_instance_objects(
        obj.name,
        ["Scene Smoke Linked", "Scene Smoke Copy"],
        [
            {"location": (0, 0, 0), "rotation": (0, 0, 0), "scale": (1, 1, 1)},
            {"location": (4, 0, 0), "rotation": (0, 0, 0), "scale": (1, 1, 1)},
        ],
        "LINKED_DATA",
    )
    assert len(copies["objects"]) == 2
    assert bpy.data.objects["Scene Smoke Linked"].data is obj.data

    collection = handler.manage_scene_collections("CREATE", "Scene Smoke Collection")
    assert collection["name"] == "Scene Smoke Collection"
    handler.manage_scene_collections("LINK_OBJECTS", "Scene Smoke Collection", [obj.name])
    assert bpy.data.collections["Scene Smoke Collection"].objects.get(obj.name) is obj

    handler.manage_object_hierarchy(
        [{"child_object_name": "Scene Smoke Linked", "parent_object_name": obj.name}],
        preserve_world_transform=True,
    )
    assert bpy.data.objects["Scene Smoke Linked"].parent is obj

    handler.manage_object_constraints(
        "Scene Smoke Copy",
        "ADD",
        {
            "name": "Smoke Copy Location",
            "type": "COPY_LOCATION",
            "target_object_name": obj.name,
            "influence": 0.5,
            "settings": {"use_x": True, "use_y": False, "use_z": False},
        },
    )
    assert bpy.data.objects["Scene Smoke Copy"].constraints.get("Smoke Copy Location") is not None

    modifier = handler.manage_modifiers(
        obj.name,
        "ADD",
        {"name": "Smoke Bevel", "type": "BEVEL", "settings": {"width": 0.05, "segments": 2}},
    )
    assert modifier["modifier"] == "Smoke Bevel"

    removed = handler.remove_scene_objects(["Scene Smoke Curve", "Scene Smoke Points"], confirm_remove=True)
    assert set(removed["removed"]) == {"Scene Smoke Curve", "Scene Smoke Points"}
    print("SCENE_SMOKE_OK")


if __name__ == "__main__":
    main()
