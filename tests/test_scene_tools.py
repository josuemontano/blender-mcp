"""Regression coverage for declarative scene composition tools."""

import asyncio

import pytest

from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import scene


SCENE_COMMANDS = {
    "create_geometry_object",
    "set_object_transform",
    "duplicate_or_instance_objects",
    "manage_scene_collections",
    "manage_object_hierarchy",
    "manage_object_constraints",
    "manage_modifiers",
    "remove_scene_objects",
}


class _Connection:
    def __init__(self) -> None:
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return {"changed_objects": [params.get("name", "Created")], "kind": params.get("geometry", {}).get("kind")}


def test_scene_tools_are_registered_and_dispatched(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})

    assert SCENE_COMMANDS <= set(scene.mcp._tool_manager._tools)
    assert SCENE_COMMANDS <= set(addon.BlenderMCPServer()._build_command_handlers())
    assert not SCENE_COMMANDS & addon.BlenderMCPServer._READ_ONLY_COMMANDS


def test_create_geometry_object_serializes_discriminated_geometry(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(scene, "get_blender_connection", lambda: connection)
    geometry = scene.MeshGeometry(
        vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        faces=[[0, 1, 2]],
    )

    result = asyncio.run(scene.create_geometry_object(ctx=None, name="Triangle", geometry=geometry))

    assert connection.calls[0][0] == "create_geometry_object"
    assert connection.calls[0][1]["geometry"]["kind"] == "MESH"
    assert result["changed_objects"] == ["Triangle"]


def test_scene_models_reject_ambiguous_or_degenerate_transforms() -> None:
    with pytest.raises(ValidationError, match="at least one transform"):
        scene.TransformPatch()
    with pytest.raises(ValidationError, match="at most one rotation"):
        scene.TransformPatch(rotation_euler=(0, 0, 0), rotation_quaternion=(1, 0, 0, 0))
    with pytest.raises(ValidationError, match="non-zero"):
        scene.TransformPatch(scale=(1, 0, 1))
    with pytest.raises(ValidationError, match="non-zero"):
        scene.InstanceTransform(scale=(1, 1, 0))


def test_point_cloud_requires_one_radius_per_point() -> None:
    with pytest.raises(ValidationError, match="one value per point"):
        scene.PointCloudGeometry(points=[(0, 0, 0), (1, 0, 0)], radii=[0.5])


def test_breaking_tool_names_are_absent() -> None:
    registered = set(scene.mcp._tool_manager._tools)
    removed = {
        "execute_blender_code",
        "viewport_overlay_toggle",
        "search_polyhaven_assets",
        "download_sketchfab_model",
        "model_mirror",
        "model_array",
        "model_radial_array",
    }

    assert not removed & registered
