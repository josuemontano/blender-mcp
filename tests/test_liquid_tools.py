"""Regression coverage for the typed phase-zero liquid MCP surface."""

# Test doubles deliberately use structural/untyped APIs matching the existing
# Blender-free test harness.
# ruff: file-ignore[float-equality-comparison, import-private-name, magic-value-comparison, missing-return-type-private-function, missing-return-type-special-method, missing-type-function-argument, missing-type-kwargs, unnecessary-dict-kwargs, undocumented-public-function]

import asyncio
import sys
import types

import pytest

from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import liquid


class _StubConnection:
    def __init__(self, result=None) -> None:
        self.result = result or {"status": "ok"}
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return self.result


def _run(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def test_liquid_patch_models_forbid_unrestricted_rna_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        liquid.LiquidSolverPatch(**{"arbitrary_rna": 1})  # pyright: ignore[reportArgumentType]


def test_solver_patch_rejects_inverted_ranges() -> None:
    with pytest.raises(ValidationError, match="timesteps_min"):
        liquid.LiquidSolverPatch(timesteps_min=5, timesteps_max=2)
    with pytest.raises(ValidationError, match="particle_min"):
        liquid.LiquidSolverPatch(particle_min=9, particle_max=4)


def test_solver_tool_serializes_only_explicit_patch_fields(monkeypatch) -> None:
    connection = _StubConnection()
    monkeypatch.setattr(liquid, "get_blender_connection", lambda: connection)

    result = _run(
        liquid.configure_liquid_solver,
        domain_object_name="Domain",
        modifier_name="Liquid Domain",
        patch=liquid.LiquidSolverPatch(resolution_max=96, flip_ratio=0.9),
    )

    assert result["ok"] is True
    assert connection.calls == [
        (
            "configure_liquid_solver",
            {
                "domain_object_name": "Domain",
                "modifier_name": "Liquid Domain",
                "patch": {"resolution_max": 96, "flip_ratio": 0.9},
            },
        )
    ]


def test_flow_tool_forwards_typed_liquid_only_settings(monkeypatch) -> None:
    connection = _StubConnection({"changed_objects": ["Pour", "Domain"]})
    monkeypatch.setattr(liquid, "get_blender_connection", lambda: connection)

    result = _run(
        liquid.add_liquid_flow,
        object_name="Pour",
        domain_object_name="Domain",
        behavior="INFLOW",
        settings=liquid.LiquidFlowPatch(use_inflow=True, subframes=2, velocity_coord=(0.0, 0.0, -1.0)),
    )

    assert result["changed_objects"] == ["Pour", "Domain"]
    assert connection.calls[0][1]["settings"] == {
        "use_inflow": True,
        "subframes": 2,
        "velocity_coord": (0.0, 0.0, -1.0),
    }


def test_read_only_liquid_tool_reports_no_changes(monkeypatch) -> None:
    connection = _StubConnection({"domains": [], "dependencies": []})
    monkeypatch.setattr(liquid, "get_blender_connection", lambda: connection)

    result = _run(liquid.get_liquid_simulation_info, scene_name="Scene")

    assert result["changed_objects"] == []
    assert connection.calls[0][0] == "get_liquid_simulation_info"


def test_all_twelve_phase_zero_commands_are_registered() -> None:
    names = {
        "get_liquid_simulation_info",
        "get_fluid_object_info",
        "create_liquid_domain",
        "fit_liquid_domain",
        "configure_liquid_solver",
        "add_liquid_flow",
        "configure_liquid_flow",
        "add_liquid_effector",
        "configure_liquid_effector",
        "configure_liquid_scope_and_boundaries",
        "estimate_liquid_resources",
        "validate_liquid_setup",
    }

    assert all(callable(getattr(liquid, name)) for name in names)
    assert set(liquid.mcp._tool_manager._tools) >= names


def _load_liquid_handler(monkeypatch):
    addon, _bpy = _load_addon(monkeypatch, data={})
    return addon, sys.modules[f"{addon.__name__}.handlers.liquid"]


class _FakeRnaProperty:
    def __init__(self, *, prop_type="FLOAT", minimum=0.0, maximum=10.0, readonly=False) -> None:
        self.type = prop_type
        self.hard_min = minimum
        self.hard_max = maximum
        self.is_readonly = readonly
        self.is_array = False
        self.array_length = 0
        self.enum_items = []


class _FakeRnaProperties(dict):
    def __iter__(self):
        return iter(self.values())


def test_liquid_handler_preflights_entire_patch_before_mutation(monkeypatch) -> None:
    _addon, handler = _load_liquid_handler(monkeypatch)
    owner = types.SimpleNamespace(
        first=1.0,
        second=2.0,
        bl_rna=types.SimpleNamespace(
            properties=_FakeRnaProperties(
                first=_FakeRnaProperty(),
                second=_FakeRnaProperty(),
            )
        ),
    )

    with pytest.raises(ValueError, match="outside Blender's RNA range"):
        handler._patch_rna(owner, {"first": 5.0, "second": 99.0}, {"first", "second"})

    assert owner.first == 1.0
    assert owner.second == 2.0


def test_liquid_commands_dispatch_and_read_only_classification(monkeypatch) -> None:
    addon, _handler = _load_liquid_handler(monkeypatch)
    server = addon.BlenderMCPServer()
    commands = server._build_command_handlers()

    assert "create_liquid_domain" in commands
    assert "validate_liquid_setup" in commands
    assert "get_liquid_simulation_info" in server._READ_ONLY_COMMANDS
    assert "estimate_liquid_resources" in server._READ_ONLY_COMMANDS
    assert "create_liquid_domain" not in server._READ_ONLY_COMMANDS
    assert "fit_liquid_domain" in server._GEOMETRY_MUTATING_COMMANDS


def test_resource_estimate_formula_is_explicit_and_conservative(monkeypatch) -> None:
    _addon, handler = _load_liquid_handler(monkeypatch)
    settings = types.SimpleNamespace(
        resolution_max=100,
        cache_frame_start=1,
        cache_frame_end=10,
        use_mesh=True,
        mesh_scale=2,
        particle_number=2,
        particle_min=8,
        particle_max=16,
        use_spray_particles=False,
        use_foam_particles=False,
        use_bubble_particles=False,
        use_tracer_particles=False,
        cache_type="REPLAY",
        cache_data_format="UNI",
    )
    obj = types.SimpleNamespace(name="Domain")
    modifier = types.SimpleNamespace(name="Liquid Domain")
    monkeypatch.setattr(handler, "_get_domain", lambda *_args: (obj, modifier, settings))
    monkeypatch.setattr(
        handler,
        "_world_bounds",
        lambda *_args: {"dimensions": [4.0, 2.0, 1.0]},
    )

    result = handler.LiquidHandlersMixin().estimate_liquid_resources("Domain", "Liquid Domain")

    assert result["estimated_grid"]["cell_size"] == pytest.approx(0.04)
    assert result["estimated_grid"]["cells_xyz"] == [100, 50, 25]
    assert result["estimated_grid"]["base_cell_count"] == 125_000
    assert result["frame_count"] == 10
    assert result["relative_cost_index"] > result["estimated_grid"]["base_cell_count"] * 10
