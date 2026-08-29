"""Server-boundary and dispatch coverage for phase-zero camera tools."""

import asyncio
import math
import sys

import pytest

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import camera

CAMERA_COMMANDS = {
    "get_camera_rig_info",
    "create_camera",
    "configure_camera",
    "set_scene_camera",
    "aim_camera",
    "create_camera_target",
    "frame_camera_on_objects",
    "create_orbit_camera_rig",
    "create_dolly_camera_rig",
    "create_crane_camera_rig",
    "create_camera_path_rig",
    "configure_camera_dof",
}


class _StubConnection:
    def __init__(self, result=None) -> None:
        self.result = result or {"status": "ok"}
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return self.result


def _run(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def test_all_phase_zero_camera_tools_are_public() -> None:
    assert all(callable(getattr(camera, name)) for name in CAMERA_COMMANDS)


def test_camera_patch_forbids_unknown_rna_and_invalid_optics() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        camera.CameraOpticsPatch(arbitrary_rna=1)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        camera.CameraOpticsPatch(lens=0)
    with pytest.raises(ValidationError, match="clip_start must be less"):
        camera.CameraOpticsPatch(clip_start=10, clip_end=1)


def test_create_camera_rejects_ambiguous_orientation_before_dispatch(monkeypatch) -> None:
    connection = _StubConnection()
    monkeypatch.setattr(camera, "get_blender_connection", lambda: connection)

    with pytest.raises(ToolError, match="only one orientation source"):
        _run(
            camera.create_camera,
            scene_name="Scene",
            collection_name="Cameras",
            name="Hero",
            rotation_euler=(0, 0, 0),
            look_at_point=(0, 0, 0),
        )

    assert connection.calls == []


def test_configure_camera_serializes_only_explicit_patch_fields(monkeypatch) -> None:
    connection = _StubConnection({"camera": "Hero", "changed_resources": ["Hero Data"]})
    monkeypatch.setattr(camera, "get_blender_connection", lambda: connection)

    result = _run(
        camera.configure_camera,
        camera_name="Hero",
        optics=camera.CameraOpticsPatch(lens=85),
        display=camera.CameraDisplayPatch(show_composition_thirds=True),
    )

    assert connection.calls == [
        (
            "configure_camera",
            {
                "camera_name": "Hero",
                "optics": {"lens": 85.0},
                "display": {"show_composition_thirds": True},
            },
        )
    ]
    assert result["changed_objects"] == ["Hero"]
    assert result["changed_resources"] == ["Hero Data"]


def test_rig_builder_defaults_are_plain_values_and_context_is_not_forwarded(monkeypatch) -> None:
    connection = _StubConnection({"changed_objects": ["Orbit Root", "Orbit Camera"]})
    monkeypatch.setattr(camera, "get_blender_connection", lambda: connection)

    _run(
        camera.create_orbit_camera_rig,
        scene_name="Scene",
        collection_name="Camera Rigs",
        rig_name="Orbit",
        pivot=(0, 0, 0),
        radius=8,
    )

    command, params = connection.calls[0]
    assert command == "create_orbit_camera_rig"
    assert "ctx" not in params
    assert params["lens"] == pytest.approx(50.0)
    assert params["azimuth"] == pytest.approx(0.0)


def test_path_tool_preflights_path_and_frame_intent(monkeypatch) -> None:
    connection = _StubConnection()
    monkeypatch.setattr(camera, "get_blender_connection", lambda: connection)

    with pytest.raises(ToolError, match="exactly one"):
        _run(
            camera.create_camera_path_rig,
            scene_name="Scene",
            collection_name="Rigs",
            rig_name="Move",
            camera_name="Hero",
        )
    with pytest.raises(ToolError, match="start_frame and end_frame"):
        _run(
            camera.create_camera_path_rig,
            scene_name="Scene",
            collection_name="Rigs",
            rig_name="Move",
            camera_name="Hero",
            path_points=[(0, 0, 0), (1, 0, 0)],
            start_frame=1,
        )

    assert connection.calls == []


def test_dispatch_advertises_all_camera_commands_and_only_inspection_is_read_only(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()

    commands = server._build_command_handlers()

    assert CAMERA_COMMANDS.issubset(commands)
    assert "get_camera_rig_info" in server._READ_ONLY_COMMANDS
    assert not (CAMERA_COMMANDS - {"get_camera_rig_info"}) & server._READ_ONLY_COMMANDS


def test_handler_camera_patch_rolls_back_assignments(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    handler = sys.modules[f"{addon.__name__}.handlers.camera"]

    class Owner:
        lens = 50.0
        shift_x = 0.0

        def __setattr__(self, name, value) -> None:
            if name == "shift_x" and math.isclose(value, 2.0):
                raise RuntimeError("assignment failed")
            object.__setattr__(self, name, value)

    owner = Owner()
    with pytest.raises(RuntimeError, match="assignment failed"):
        handler._patch_values(owner, {"lens": 85.0, "shift_x": 2.0}, {"lens", "shift_x"})

    assert owner.lens == pytest.approx(50.0)
    assert owner.shift_x == pytest.approx(0.0)


def test_handler_binary_solver_finds_smallest_fitting_value(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    handler = sys.modules[f"{addon.__name__}.handlers.camera"]

    solved = handler._binary_smallest_fit(lambda value: value >= 7.5, 0.0, 1.0)

    assert solved == pytest.approx(7.5)
