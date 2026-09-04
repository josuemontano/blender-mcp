# ruff: file-ignore[import-private-name, missing-return-type-private-function, missing-type-function-argument, undocumented-public-function, yoda-conditions]
"""Regression coverage for render, view-layer, and pass tools."""

import asyncio

import pytest

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import rendering

RENDER_COMMANDS = {
    "inspect_render_setup",
    "configure_render_settings",
    "manage_view_layers",
    "render_scene",
}


class _Connection:
    def __init__(self) -> None:
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return {"changed_resources": [params.get("scene_name", "Scene")]}


def test_render_tools_are_registered_and_dispatched(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()

    assert RENDER_COMMANDS <= set(rendering.mcp._tool_manager._tools)
    assert RENDER_COMMANDS <= set(server._build_command_handlers())
    assert "inspect_render_setup" in server._READ_ONLY_COMMANDS
    assert "configure_render_settings" not in server._READ_ONLY_COMMANDS
    assert "manage_view_layers" not in server._READ_ONLY_COMMANDS


def test_render_settings_patch_is_strict_and_bounded() -> None:
    with pytest.raises(ValidationError, match="at least one field"):
        rendering.RenderSettingsPatch()
    with pytest.raises(ValidationError):
        rendering.RenderSettingsPatch(resolution_x=1)
    with pytest.raises(ValidationError):
        rendering.RenderSettingsPatch(frame_start=20, frame_end=10)
    with pytest.raises(ValidationError):
        rendering.RenderSettingsPatch(unknown=True)


def test_view_layer_patch_is_strict_and_cryptomatte_depth_is_even() -> None:
    with pytest.raises(ValidationError, match="at least one field"):
        rendering.ViewLayerPatch()
    with pytest.raises(ValidationError):
        rendering.ViewLayerPatch(pass_cryptomatte_depth=3)
    patch = rendering.ViewLayerPatch(use_pass_position=True, pass_cryptomatte_depth=8)
    assert patch.use_pass_position is True


def test_configure_render_settings_serializes_patch(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(rendering, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        rendering.configure_render_settings(
            ctx=None,
            scene_name="Scene",
            patch=rendering.RenderSettingsPatch(
                engine="CYCLES",
                cycles_samples=64,
                cycles_use_denoising=True,
                compression=25,
            ),
        )
    )

    command, params = connection.calls[0]
    assert command == "configure_render_settings"
    assert params["patch"] == {
        "engine": "CYCLES",
        "compression": 25,
        "cycles_samples": 64,
        "cycles_use_denoising": True,
    }
    assert result["changed_resources"] == ["Scene"]


def test_view_layer_and_render_confirmation_rules(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(rendering, "get_blender_connection", lambda: connection)

    with pytest.raises(ToolError, match="PATCH requires"):
        asyncio.run(
            rendering.manage_view_layers(
                ctx=None,
                scene_name="Scene",
                action="PATCH",
                view_layer_name="Main",
            )
        )
    with pytest.raises(ToolError, match="does not accept"):
        asyncio.run(
            rendering.manage_view_layers(
                ctx=None,
                scene_name="Scene",
                action="REMOVE",
                view_layer_name="Main",
                patch=rendering.ViewLayerPatch(use=False),
                confirm_remove=True,
            )
        )
    with pytest.raises(ToolError, match="confirm_render"):
        asyncio.run(rendering.render_scene(ctx=None, scene_name="Scene", filepath="/tmp/output.png"))
    assert connection.calls == []
