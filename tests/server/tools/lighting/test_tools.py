"""Server-boundary, packaging, and dispatch coverage for production lighting tools."""

import asyncio
import inspect

from pathlib import Path

import pytest

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import lighting
from blender_mcp.server.tools.lighting import _shared

LIGHTING_COMMANDS = {
    "list_lights",
    "inspect_light",
    "inspect_lighting_setup",
    "validate_lighting_setup",
    "create_light",
    "configure_light",
    "aim_light",
    "configure_light_linking",
    "create_studio_lighting",
    "configure_world_background",
    "configure_hdri_environment",
    "configure_procedural_sky",
    "configure_lighting_quality",
    "configure_color_management",
    "render_lighting_preview",
}
READ_ONLY_LIGHTING_COMMANDS = {
    "list_lights",
    "inspect_light",
    "inspect_lighting_setup",
    "validate_lighting_setup",
}


class StubConnection:
    """Record commands sent through a lighting tool."""

    def __init__(self, result=None) -> None:
        self.result = result or {"status": "ok"}
        self.calls = []

    def send_command(self, command, params):
        """Record and return one synthetic Blender response."""
        self.calls.append((command, params))
        return self.result


def run_tool(function, **kwargs):
    """Run one async FastMCP tool function without a request context."""
    return asyncio.run(function(ctx=None, **kwargs))


def test_all_lighting_commands_are_public_and_grouped() -> None:
    assert all(callable(getattr(lighting, name)) for name in LIGHTING_COMMANDS)
    assert {"construction.py", "environment.py", "inspection.py", "rendering.py"}.issubset(
        {path.name for path in Path(lighting.__file__).parent.iterdir()}
    )


def test_implementation_identifiers_do_not_use_phase_names() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (
            Path("src/blender_mcp/server/tools/lighting"),
            Path("src/blender_mcp/bundled/addon/handlers/lighting"),
        )
        for path in root.glob("*.py")
    )
    assert "PhaseZero" not in source
    assert "PhaseOne" not in source
    assert "phase_0" not in source
    assert "phase_1" not in source


def test_polyhaven_hdri_uses_the_managed_environment_handler() -> None:
    source = Path("src/blender_mcp/bundled/addon/handlers/polyhaven.py").read_text(encoding="utf-8")

    assert "self.configure_hdri_environment(" in source
    assert "bpy.data.worlds[0]" not in source
    assert "tempfile._cleanup" not in source


def test_light_models_reject_unknown_fields_and_invalid_ranges() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        lighting.LightPatch(arbitrary_rna=1)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        lighting.LightSettings(color=(1.2, 0.5, 0.5))
    with pytest.raises(ValidationError):
        lighting.ProceduralSkySettings(sun_size=0)
    with pytest.raises(ValidationError):
        lighting.ProceduralSkySettings(sun_size=2)
    with pytest.raises(ValidationError):
        lighting.ProceduralSkySettings(sun_intensity=1001)
    with pytest.raises(ValidationError):
        lighting.ProceduralSkySettings(altitude=100001)


def test_preview_dispatch_is_async_and_paths_are_distinct() -> None:
    assert inspect.iscoroutinefunction(lighting.render_lighting_preview)
    with pytest.raises(ToolError, match="distinct output path"):
        run_tool(
            lighting.render_lighting_preview,
            scene_name="Scene",
            camera_name="Camera",
            frame=1,
            target_engine="BOTH",
            cycles_output_path="/tmp/shared.png",
            eevee_output_path="/tmp/shared.png",
        )


def test_configure_light_sends_only_explicit_patch_fields(monkeypatch) -> None:
    connection = StubConnection({"object": "Key", "changed_resources": ["Key Light"]})
    monkeypatch.setattr(_shared, "get_blender_connection", lambda: connection)

    result = run_tool(
        lighting.configure_light,
        light_name="Key",
        patch=lighting.LightPatch(energy=750, use_shadow=False),
    )

    assert connection.calls == [
        ("configure_light", {"light_name": "Key", "patch": {"energy": 750.0, "use_shadow": False}})
    ]
    assert result["changed_objects"] == ["Key"]
    assert result["changed_resources"] == ["Key Light"]


def test_aim_light_rejects_ambiguous_target_before_dispatch(monkeypatch) -> None:
    connection = StubConnection()
    monkeypatch.setattr(_shared, "get_blender_connection", lambda: connection)

    with pytest.raises(ToolError, match="exactly one"):
        run_tool(
            lighting.aim_light,
            scene_name="Scene",
            light_name="Key",
            target_point=(0, 0, 0),
            target_object_name="Subject",
        )

    assert connection.calls == []


def test_create_studio_lighting_dispatches_rig_then_preview(monkeypatch) -> None:
    connection = StubConnection({"lights": [], "changed_objects": []})
    monkeypatch.setattr(_shared, "get_blender_connection", lambda: connection)

    result = run_tool(
        lighting.create_studio_lighting,
        scene_name="Scene",
        target_object_name="Product",
        camera_name="Camera",
        frame=1,
        preview_output_path="/tmp/studio_preview.png",
    )

    assert [call[0] for call in connection.calls] == ["create_studio_lighting", "render_lighting_preview"]
    rig_command, preview_command = connection.calls
    assert rig_command[1] == {
        "scene_name": "Scene",
        "target_object_name": "Product",
        "camera_name": "Camera",
        "mood": "SOFT",
        "key_ratio": None,
        "rig_name": None,
        "collection_name": "Studio Lighting",
    }
    assert preview_command[1]["camera_name"] == "Camera"
    assert preview_command[1]["target_engine"] == "EEVEE"
    assert preview_command[1]["output_paths"] == {"EEVEE": "/tmp/studio_preview.png"}
    assert [item["data"] for item in result] == [{"lights": []}, {"lights": []}]


def test_lighting_quality_expands_strict_agent_payload(monkeypatch) -> None:
    connection = StubConnection({"changed_resources": ["Scene"]})
    monkeypatch.setattr(_shared, "get_blender_connection", lambda: connection)

    run_tool(
        lighting.configure_lighting_quality,
        scene_name="Scene",
        target_engine="BOTH",
        cycles=lighting.CyclesLightingQuality(samples=128),
        eevee=lighting.EeveeLightingQuality(render_samples=64, use_fast_gi=True),
    )

    assert connection.calls == [
        (
            "configure_lighting_quality",
            {
                "scene_name": "Scene",
                "target_engine": "BOTH",
                "preset": None,
                "cycles": {"samples": 128},
                "eevee": {"render_samples": 64, "use_fast_gi": True},
            },
        )
    ]


def test_hdri_requires_an_absolute_hdr_or_exr_path_before_dispatch(monkeypatch) -> None:
    connection = StubConnection()
    monkeypatch.setattr(_shared, "get_blender_connection", lambda: connection)

    with pytest.raises(ToolError, match="absolute"):
        run_tool(lighting.configure_hdri_environment, scene_name="Scene", image_path="studio.hdr")
    with pytest.raises(ToolError, match=".hdr or .exr"):
        run_tool(lighting.configure_hdri_environment, scene_name="Scene", image_path="/tmp/studio.png")

    assert connection.calls == []


def test_dispatch_advertises_lighting_and_marks_only_inspection_read_only(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()

    commands = server._build_command_handlers()

    assert LIGHTING_COMMANDS.issubset(commands)
    assert READ_ONLY_LIGHTING_COMMANDS.issubset(server._READ_ONLY_COMMANDS)
    assert not (LIGHTING_COMMANDS - READ_ONLY_LIGHTING_COMMANDS) & server._READ_ONLY_COMMANDS
