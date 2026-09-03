"""Public schema, package organization, transport, and dispatch coverage for PBR texturing."""

import asyncio
import inspect

from pathlib import Path

import pytest

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import texture
from blender_mcp.server.tools.texture import _shared

TEXTURE_COMMANDS = {
    "list_materials",
    "inspect_material",
    "create_pbr_material",
    "configure_pbr_material",
    "assign_material",
    "configure_texture_mapping",
    "list_texture_images",
    "load_texture_image",
    "configure_texture_image",
    "apply_pbr_texture_set",
    "save_texture_image",
    "render_pbr_material_preview",
    "manage_uv_maps",
    "set_uv_seams",
    "unwrap_uvs",
    "optimize_uv_layout",
    "inspect_uv_layout",
    "bake_texture_map",
    "validate_pbr_asset",
}
READ_ONLY_TEXTURE_COMMANDS = {
    "list_materials",
    "inspect_material",
    "list_texture_images",
    "inspect_uv_layout",
    "validate_pbr_asset",
}


class StubConnection:
    """Record commands sent through a texturing tool."""

    def __init__(self, result=None):
        self.result = result or {"status": "ok"}
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return self.result


def run_tool(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def test_all_texture_commands_are_public_and_grouped_by_responsibility():
    assert all(callable(getattr(texture, name)) for name in TEXTURE_COMMANDS)
    expected_modules = {"materials.py", "images.py", "uv.py", "baking.py", "previews.py", "validation.py"}
    assert expected_modules.issubset({path.name for path in Path(texture.__file__).parent.iterdir()})


def test_texture_implementation_identifiers_are_domain_named():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (
            Path("src/blender_mcp/server/tools/texture"),
            Path("src/blender_mcp/bundled/addon/handlers/texture"),
        )
        for path in root.glob("*.py")
    )
    assert "PhaseZero" not in source
    assert "PhaseOne" not in source
    assert "phase_0" not in source
    assert "phase_1" not in source


def test_material_patch_is_strict_and_rejects_nonfinite_values():
    with pytest.raises(ValidationError):
        texture.PBRMaterialSettings(arbitrary_rna=True)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        texture.PBRMaterialSettings(roughness=float("nan"))
    with pytest.raises(ValidationError):
        texture.PBRMaterialSettings(base_color=(1.2, 0.2, 0.2, 1.0))


def test_configure_material_sends_only_explicit_fields(monkeypatch):
    connection = StubConnection({"material": "Paint", "changed_resources": ["Paint"]})
    monkeypatch.setattr(_shared, "get_blender_connection", lambda: connection)

    result = run_tool(
        texture.configure_pbr_material,
        material_name="Paint",
        patch=texture.PBRMaterialSettings(roughness=0.25, metallic=0.8),
    )

    assert connection.calls == [
        (
            "configure_pbr_material",
            {"material_name": "Paint", "patch": {"metallic": 0.8, "roughness": 0.25}, "target_engine": "BOTH"},
        )
    ]
    assert result["changed_resources"] == ["Paint"]


def test_texture_set_rejects_ambiguous_semantic_channels():
    with pytest.raises(ValidationError, match="roughness or glossiness"):
        texture.TextureSetFiles(roughness="/tmp/r.png", glossiness="/tmp/g.png")
    with pytest.raises(ValidationError, match="normal_opengl or normal_directx"):
        texture.TextureSetFiles(normal_opengl="/tmp/n.png", normal_directx="/tmp/n_dx.png")


def test_destructive_or_expensive_inputs_are_gated_before_dispatch(monkeypatch):
    connection = StubConnection()
    monkeypatch.setattr(_shared, "get_blender_connection", lambda: connection)

    with pytest.raises(ToolError, match="confirm=True"):
        run_tool(texture.bake_texture_map, object_name="Low", map_type="NORMAL", output_path="/tmp/n.png")
    with pytest.raises(ToolError, match="confirm_cycles"):
        run_tool(texture.render_pbr_material_preview, material_name="Paint", target_engine="CYCLES")

    assert connection.calls == []


def test_texture_tools_are_async_and_dispatch_is_complete(monkeypatch):
    assert all(inspect.iscoroutinefunction(getattr(texture, name)) for name in TEXTURE_COMMANDS)
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()

    commands = server._build_command_handlers()

    assert TEXTURE_COMMANDS.issubset(commands)
    assert READ_ONLY_TEXTURE_COMMANDS.issubset(server._READ_ONLY_COMMANDS)
    assert not (TEXTURE_COMMANDS - READ_ONLY_TEXTURE_COMMANDS) & server._READ_ONLY_COMMANDS
