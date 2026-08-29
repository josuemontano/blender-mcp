"""Regression coverage for the typed phase-one liquid MCP surface."""

# Test doubles deliberately use structural/untyped APIs matching the existing
# Blender-free test harness.
# ruff: file-ignore[import-private-name, magic-value-comparison, missing-return-type-private-function, missing-type-function-argument, missing-type-kwargs, unnecessary-dict-kwargs, undocumented-public-function]

import asyncio
import sys

import pytest

from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import liquid_phase1


class _StubConnection:
    def __init__(self, result=None) -> None:
        self.result = result or {"status": "ok"}
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return self.result


def _run(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def _load_phase1_handler(monkeypatch):
    addon, _bpy = _load_addon(monkeypatch, data={})
    return addon, sys.modules[f"{addon.__name__}.handlers.liquid_phase1"]


def test_all_eleven_phase_one_commands_are_registered() -> None:
    names = {
        "configure_liquid_mesh",
        "configure_liquid_secondary_particles",
        "configure_liquid_diffusion",
        "animate_liquid_flow",
        "create_liquid_guide",
        "configure_liquid_force_fields",
        "create_liquid_material",
        "create_secondary_particle_render_setup",
        "sample_liquid_simulation",
        "manage_liquid_cache",
        "remove_fluid_components",
    }

    assert all(callable(getattr(liquid_phase1, name)) for name in names)
    assert set(liquid_phase1.mcp._tool_manager._tools) >= names


def test_phase_one_models_reject_unknown_and_inconsistent_values() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        liquid_phase1.LiquidMeshPatch(**{"smoke_only": True})  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValidationError, match="minimum must be <= maximum"):
        liquid_phase1.LiquidSecondaryParticlePatch(sndparticle_life_min=4, sndparticle_life_max=2)
    with pytest.raises(ValidationError, match="supplied together"):
        liquid_phase1.LiquidDiffusionConfig(dynamic_viscosity_pa_s=0.001)
    with pytest.raises(ValidationError, match="exactly one"):
        liquid_phase1.LiquidFlowKeyframe(frame=1, use_inflow=True, velocity_factor=1.0)


def test_mesh_tool_serializes_only_supplied_patch(monkeypatch) -> None:
    connection = _StubConnection({"changed_objects": ["Domain"]})
    monkeypatch.setattr(liquid_phase1, "get_blender_connection", lambda: connection, raising=False)
    monkeypatch.setattr(
        liquid_phase1,
        "_call",
        lambda command, params, changed_objects=None: {
            "command": command,
            "params": params,
            "changed_objects": changed_objects,
        },
    )

    result = _run(
        liquid_phase1.configure_liquid_mesh,
        domain_object_name="Domain",
        modifier_name="Liquid Domain",
        patch=liquid_phase1.LiquidMeshPatch(use_mesh=True, mesh_scale=3),
    )

    assert result["command"] == "configure_liquid_mesh"
    assert result["params"]["patch"] == {"use_mesh": True, "mesh_scale": 3}


def test_dynamic_viscosity_conversion_is_explicit(monkeypatch) -> None:
    _addon, handler = _load_phase1_handler(monkeypatch)

    patch, source, evidence = handler._expand_viscosity_config(
        {"dynamic_viscosity_pa_s": 0.001, "density_kg_m3": 1000.0}
    )

    assert source == "SI_DYNAMIC_DENSITY"
    assert evidence["kinematic_viscosity_m2_s"] == pytest.approx(1e-6)
    assert patch["viscosity_base"] == pytest.approx(1.0)
    assert patch["viscosity_exponent"] == 6
    assert patch["use_diffusion"] is True


def test_phase_one_commands_dispatch_and_nested_targets_are_resolved(monkeypatch) -> None:
    addon, _handler = _load_phase1_handler(monkeypatch)
    domain = type("Object", (), {"name": "Domain"})()
    force = type("Object", (), {"name": "Wind"})()
    addon.bpy.data.objects = {"Domain": domain, "Wind": force}
    server = addon.BlenderMCPServer()

    commands = server._build_command_handlers()
    targets = server._resolve_targets(
        {
            "domain_object_name": "Domain",
            "fields": [{"object_name": "Wind"}],
            "targets": [{"object_name": "Domain", "modifier_name": "Liquid Domain"}],
        }
    )

    assert "configure_liquid_mesh" in commands
    assert "manage_liquid_cache" in commands
    assert [item.name for item in targets] == ["Domain", "Wind"]


def test_liquid_cache_status_dispatch_is_read_only(monkeypatch) -> None:
    addon, _handler = _load_phase1_handler(monkeypatch)
    server = addon.BlenderMCPServer()

    result = server._run_handler(
        "manage_liquid_cache",
        lambda **params: {"action": params["action"], "changed_objects": []},
        {"action": "STATUS"},
    )

    assert result == {"action": "STATUS", "changed_objects": []}


def test_particle_role_classification_never_guesses_unknown_systems(monkeypatch) -> None:
    _addon, handler = _load_phase1_handler(monkeypatch)
    spray = type("System", (), {"name": "Surface Spray", "settings": type("Settings", (), {"name": "Output"})()})()
    unknown = type("System", (), {"name": "Particles", "settings": type("Settings", (), {"name": "Generic"})()})()

    assert handler._particle_role(spray) == "SPRAY"
    assert handler._particle_role(unknown) == "UNKNOWN"
