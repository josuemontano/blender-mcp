"""Regression coverage for the typed phase-two liquid MCP surface."""

# Test doubles deliberately use structural/untyped APIs matching the existing
# Blender-free test harness.
# ruff: file-ignore[import-private-name, missing-return-type-private-function, missing-type-function-argument, missing-type-kwargs, undocumented-public-function]

import asyncio
import sys
import types

import pytest

from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import liquid_phase2


class _StubConnection:
    def __init__(self, result=None) -> None:
        self.result = result or {"status": "ok"}
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return self.result


def _run(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def _load_phase2_handler(monkeypatch):
    addon, _bpy = _load_addon(monkeypatch, data={})
    return addon, sys.modules[f"{addon.__name__}.handlers.liquid_phase2"]


def test_all_five_phase_two_commands_are_registered() -> None:
    names = {
        "create_liquid_proxy_rig",
        "duplicate_liquid_setup_variant",
        "prepare_liquid_render_mesh",
        "export_liquid_simulation",
        "analyze_liquid_performance",
    }

    assert all(callable(getattr(liquid_phase2, name)) for name in names)
    assert set(liquid_phase2.mcp._tool_manager._tools) >= names


def test_phase_two_models_reject_unknown_and_inconsistent_values() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        liquid_phase2.ProxyFlowSettings(smoke_density=1.0)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError, match="subdivision_levels"):
        liquid_phase2.LiquidRenderFinish(subdivision_render_levels=2)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        liquid_phase2.ProxyFlowSettings(surface_distance=-1.0)


def test_proxy_tool_serializes_explicit_typed_settings(monkeypatch) -> None:
    connection = _StubConnection({"changed_objects": ["Proxy", "Domain"]})
    monkeypatch.setattr(liquid_phase2, "get_blender_connection", lambda: connection, raising=False)
    monkeypatch.setattr(
        liquid_phase2,
        "_call",
        lambda command, params, changed_objects=None: {
            "command": command,
            "params": params,
            "changed_objects": changed_objects,
        },
    )

    result = _run(
        liquid_phase2.create_liquid_proxy_rig,
        scene_name="Scene",
        source_object_name="Character",
        proxy_object_name="Character Liquid Proxy",
        domain_object_name="Domain",
        domain_modifier_name="Liquid Domain",
        role="EFFECTOR",
        geometry="CAPSULE",
        effector_settings=liquid_phase2.ProxyEffectorSettings(subframes=3),
        validation_frames=[1, 12],
    )

    assert result["command"] == "create_liquid_proxy_rig"
    assert result["params"]["effector_settings"] == {"subframes": 3}
    assert result["params"]["validation_frames"] == [1, 12]


def test_phase_two_dispatch_and_dynamic_read_only_classification(monkeypatch) -> None:
    addon, _handler = _load_phase2_handler(monkeypatch)
    domain = type("Object", (), {"name": "Domain"})()
    source = type("Object", (), {"name": "Source"})()
    addon.bpy.data.objects = {"Domain": domain, "Source": source}
    server = addon.BlenderMCPServer()

    assert "create_liquid_proxy_rig" in server._build_command_handlers()
    assert "analyze_liquid_performance" in server._build_command_handlers()
    targets = server._resolve_targets({"domain_object_name": "Domain", "source_object_name": "Source"})
    assert {item.name for item in targets} == {"Domain", "Source"}

    read_only_result = server._run_handler(
        "analyze_liquid_performance",
        lambda **_params: {"changed_objects": []},
        {"measure_replay_evaluation": False},
    )
    assert read_only_result == {"changed_objects": []}


def test_export_axis_validation_and_cost_helpers_are_explicit(monkeypatch) -> None:
    _addon, handler = _load_phase2_handler(monkeypatch)

    with pytest.raises(ValueError, match="different axes"):
        handler._validate_axes("X", "NEGATIVE_X")

    assert handler._bounds_volume({"dimensions": [4.0, 2.0, 0.5]}) == pytest.approx(4.0)


def test_performance_analysis_rejects_unbounded_dependency_sets(monkeypatch) -> None:
    _addon, handler = _load_phase2_handler(monkeypatch)
    obj = types.SimpleNamespace(name="Domain")
    modifier = types.SimpleNamespace(name="Liquid Domain")
    settings = types.SimpleNamespace()
    monkeypatch.setattr(handler, "_get_domain", lambda *_args: (obj, modifier, settings))
    monkeypatch.setattr(
        handler,
        "_dependency_objects",
        lambda _settings: [(types.SimpleNamespace(name=f"Flow {index}"), "FLOW", None) for index in range(3)],
    )

    with pytest.raises(ValueError, match="max_dependency_objects=2"):
        handler.LiquidPhaseTwoHandlersMixin().analyze_liquid_performance(
            "Domain", "Liquid Domain", max_dependency_objects=2
        )


def test_alembic_particles_only_export_is_rejected_explicitly(monkeypatch) -> None:
    _addon, handler = _load_phase2_handler(monkeypatch)
    scene = types.SimpleNamespace(objects={"Domain"})
    obj = types.SimpleNamespace(name="Domain")
    modifier = types.SimpleNamespace(name="Liquid Domain")
    settings = types.SimpleNamespace(
        use_mesh=True,
        has_cache_baked_mesh=True,
        has_cache_baked_particles=True,
    )
    monkeypatch.setattr(handler, "_get_scene", lambda _name: scene)
    monkeypatch.setattr(handler, "_get_domain", lambda *_args: (obj, modifier, settings))

    with pytest.raises(ValueError, match="particles-only"):
        handler.LiquidPhaseTwoHandlersMixin().export_liquid_simulation(
            "Scene",
            "Domain",
            "Liquid Domain",
            "/tmp/liquid.abc",
            "ALEMBIC",
            1,
            2,
            include_surface=False,
            include_secondary_particles=True,
        )
