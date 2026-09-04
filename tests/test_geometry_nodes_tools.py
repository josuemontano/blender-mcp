"""Regression coverage for the structured Geometry Nodes tool surface."""

import asyncio
import sys

import pytest

from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import geometry_nodes

FOUNDATION_COMMANDS = {
    "list_procedural_systems",
    "get_geometry_node_graph",
    "get_geometry_node_type_info",
    "create_geometry_node_group",
    "attach_geometry_nodes_modifier",
    "edit_node_group_interface",
    "patch_geometry_node_graph",
    "set_geometry_nodes_inputs",
    "manage_geometry_nodes_modifier",
    "copy_geometry_node_group",
    "evaluate_procedural_geometry",
    "validate_geometry_node_graph",
}

WORKFLOW_COMMANDS = {
    "create_procedural_scatter",
    "create_curve_generator",
    "create_procedural_array",
    "create_surface_paneling",
    "create_procedural_boolean",
    "create_procedural_deformer",
    "create_volume_generator",
    "manage_named_attributes",
    "manage_procedural_instances",
    "run_geometry_nodes_tool",
    "publish_procedural_asset",
}


def _run(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def test_all_planned_geometry_nodes_commands_are_registered() -> None:
    names = FOUNDATION_COMMANDS | WORKFLOW_COMMANDS

    assert all(callable(getattr(geometry_nodes, name)) for name in names)
    assert set(geometry_nodes.mcp._tool_manager._tools) >= names


def test_geometry_nodes_dispatch_and_read_only_contract(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()
    names = FOUNDATION_COMMANDS | WORKFLOW_COMMANDS
    read_only = {
        "list_procedural_systems",
        "get_geometry_node_graph",
        "get_geometry_node_type_info",
        "evaluate_procedural_geometry",
        "validate_geometry_node_graph",
    }

    assert set(server._build_command_handlers()) >= names
    assert read_only <= server._READ_ONLY_COMMANDS
    assert not (names - read_only) & server._READ_ONLY_COMMANDS


def test_interface_and_graph_models_reject_unsafe_shapes() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        geometry_nodes.InterfaceSocketSpec(
            name="Density",
            direction="INPUT",
            socket_type="NodeSocketFloat",
            arbitrary_rna=True,  # pyright: ignore[reportCallIssue]
        )
    with pytest.raises(ValidationError, match="min_value"):
        geometry_nodes.InterfaceSocketSpec(
            name="Density",
            direction="INPUT",
            socket_type="NodeSocketFloat",
            min_value=2.0,
            max_value=1.0,
        )
    with pytest.raises(ValidationError):
        geometry_nodes.GraphEdit(operation="ADD_NODE", properties={"value": float("inf")})


def test_create_group_serializes_explicit_interface(monkeypatch) -> None:
    authoring = sys.modules["blender_mcp.server.tools.geometry_nodes.authoring"]
    calls = []
    monkeypatch.setattr(
        authoring,
        "call_geometry_nodes",
        lambda command, params, **kwargs: calls.append((command, params, kwargs)) or {"ok": True},
    )

    result = _run(
        geometry_nodes.create_geometry_node_group,
        name="Scatter Controls",
        sockets=[
            geometry_nodes.InterfaceSocketSpec(
                name="Density",
                direction="INPUT",
                socket_type="NodeSocketFloat",
                default_value=10.0,
                min_value=0.0,
            )
        ],
    )

    assert result == {"ok": True}
    assert calls[0][0] == "create_geometry_node_group"
    assert calls[0][1]["sockets"][0]["socket_type"] == "NodeSocketFloat"
    assert calls[0][2]["changed_resources"] == ["Scatter Controls"]


def test_destructive_geometry_nodes_actions_require_confirmation() -> None:
    with pytest.raises(ValueError, match="confirm_destructive"):
        _run(
            geometry_nodes.manage_geometry_nodes_modifier,
            object_name="Cube",
            modifier_name="GeometryNodes",
            action="APPLY",
        )
    with pytest.raises(ValueError, match="confirm_destructive"):
        _run(
            geometry_nodes.run_geometry_nodes_tool,
            node_group_name="Cleanup",
            object_names=["Cube"],
        )
    with pytest.raises(ValueError, match="confirm_destructive"):
        _run(
            geometry_nodes.manage_named_attributes,
            object_name="Cube",
            action="REMOVE",
            attribute_name="mask",
        )


def test_attach_requires_exactly_one_group_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _run(geometry_nodes.attach_geometry_nodes_modifier, object_name="Cube")
    with pytest.raises(ValueError, match="exactly one"):
        _run(
            geometry_nodes.attach_geometry_nodes_modifier,
            object_name="Cube",
            node_group_name="Existing",
            new_group_name="New",
        )


def test_workflow_request_does_not_leak_mcp_context(monkeypatch) -> None:
    workflows = sys.modules["blender_mcp.server.tools.geometry_nodes.workflows"]
    calls = []

    async def fake_build(command, params, object_name, group_name):
        await asyncio.sleep(0)
        calls.append((command, params, object_name, group_name))
        return {"ok": True}

    monkeypatch.setattr(workflows, "_build", fake_build)
    _run(
        geometry_nodes.create_procedural_array,
        object_name="Layout",
        group_name="Radial Layout",
        source_name="Chair",
        layout="RADIAL",
        count=8,
        endpoint_policy="EXCLUDE_END",
    )

    assert calls[0][0] == "create_procedural_array"
    assert "ctx" not in calls[0][1]
    assert calls[0][1]["endpoint_policy"] == "EXCLUDE_END"


def test_copy_group_serializes_object_duplication_policy(monkeypatch) -> None:
    modifiers = sys.modules["blender_mcp.server.tools.geometry_nodes.modifiers"]
    calls = []
    monkeypatch.setattr(
        modifiers,
        "call_geometry_nodes",
        lambda command, params, **kwargs: calls.append((command, params, kwargs)) or {"ok": True},
    )

    result = _run(
        geometry_nodes.copy_geometry_node_group,
        node_group_name="Shared Scatter",
        new_name="Independent Scatter",
        duplicate_object_name="Forest",
        duplicated_object_name="Forest Variant",
        copy_object_data=True,
        copy_action=True,
        reassign_duplicate_modifiers=False,
        collision_policy="UNIQUE",
    )

    assert result == {"ok": True}
    assert calls[0][0] == "copy_geometry_node_group"
    assert calls[0][1] == {
        "node_group_name": "Shared Scatter",
        "new_name": "Independent Scatter",
        "reassign_modifiers": [],
        "duplicate_object_name": "Forest",
        "duplicated_object_name": "Forest Variant",
        "copy_object_data": True,
        "copy_action": True,
        "reassign_duplicate_modifiers": False,
        "collision_policy": "UNIQUE",
    }
    assert calls[0][2]["changed_objects"] == ["Forest Variant"]
