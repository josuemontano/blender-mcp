# ruff: file-ignore[too-many-locals]
"""Run with Blender 5.1+ to smoke-test advanced Geometry Nodes workflows."""

from __future__ import annotations

import importlib.util
import sys

from pathlib import Path

import bpy

addon_path = Path(__file__).resolve().parents[1] / "src" / "blender_mcp" / "bundled" / "addon" / "__init__.py"
package_name = "blender_mcp_geometry_nodes_smoke"
spec = importlib.util.spec_from_file_location(
    package_name, addon_path, submodule_search_locations=[str(addon_path.parent)]
)
assert spec is not None
addon = importlib.util.module_from_spec(spec)
sys.modules[package_name] = addon
spec.loader.exec_module(addon)

from blender_mcp_geometry_nodes_smoke.handlers.geometry_nodes import (  # ruff: ignore[module-import-not-at-top-of-file]
    GeometryNodesHandlersMixin,
)

MAX_REPEAT_ITERATIONS = 256
EXPECTED_ITERATION_STATE_STEPS = 8
EXPECTED_ZONE_NODE_COUNT = 4


class GeometryNodesSmokeHarness(GeometryNodesHandlersMixin):
    """Expose the Geometry Nodes handler mixins for direct Blender smoke testing."""


def main() -> None:
    """Exercise zones, bake inspection/execution, performance analysis, and delivery."""
    handler = GeometryNodesSmokeHarness()
    source = bpy.context.active_object
    source.name = "Advanced Geometry Nodes Source"
    collection_name = source.users_collection[0].name

    created = handler.create_geometry_node_group("Advanced Geometry Nodes", geometry_types=["MESH"])
    assert created["created"]
    attached = handler.attach_geometry_nodes_modifier(
        source.name,
        node_group_name="Advanced Geometry Nodes",
        modifier_name="Advanced Geometry Nodes",
    )
    assert attached["modifier"] == "Advanced Geometry Nodes"

    repeated = handler.create_repeat_zone(
        "Advanced Geometry Nodes",
        state_items=[
            {"name": "Geometry", "socket_type": "GEOMETRY"},
            {"name": "Offset", "socket_type": "VECTOR"},
        ],
        iterations=4,
        graph_operations=[
            {
                "operation": "ADD_LINK",
                "from_node": "Group Input",
                "from_socket_index": 0,
                "to_node": "Repeat Input",
                "to_socket_index": 1,
            },
            {
                "operation": "ADD_LINK",
                "from_node": "Repeat Output",
                "from_socket_index": 0,
                "to_node": "Group Output",
                "to_socket_index": 0,
            },
        ],
    )
    assert repeated["maximum_iterations"] == MAX_REPEAT_ITERATIONS
    assert repeated["complexity_estimate"]["iteration_state_steps"] == EXPECTED_ITERATION_STATE_STEPS

    simulated = handler.create_simulation_zone(
        "Advanced Geometry Nodes",
        state_items=[
            {"name": "Geometry", "socket_type": "GEOMETRY"},
            {"name": "Age", "socket_type": "FLOAT", "attribute_domain": "POINT"},
        ],
        frame_start=1,
        frame_end=2,
    )
    assert simulated["cache_status"] == "NOT_BAKED_BY_THIS_OPERATION"
    graph = handler.get_geometry_node_graph("Advanced Geometry Nodes", sections=["NODES"], limit=100)
    zones = [item["zone"] for item in graph["graph_items"] if "zone" in item]
    assert len(zones) == EXPECTED_ZONE_NODE_COUNT
    assert all(zone["paired"] for zone in zones)

    inspected = handler.manage_geometry_nodes_bake(
        source.name,
        "Advanced Geometry Nodes",
        action="INSPECT",
    )
    simulation_bakes = [item for item in inspected["bakes"] if item["node_type"] == "GeometryNodeSimulationOutput"]
    assert len(simulation_bakes) == 1
    bake_id = simulation_bakes[0]["bake_id"]
    baked = handler.manage_geometry_nodes_bake(
        source.name,
        "Advanced Geometry Nodes",
        action="BAKE",
        bake_id=bake_id,
        frame_start=1,
        frame_end=1,
        bake_target="PACKED",
        max_frames=1,
        max_bytes=10_000_000,
        time_limit_seconds=10.0,
        confirm_bake=True,
    )
    assert "FINISHED" in baked["operator_result"]

    analysis = handler.analyze_procedural_performance(
        source.name,
        "Advanced Geometry Nodes",
        frames=[1],
        repetitions=1,
        time_limit_seconds=10.0,
    )
    assert analysis["whole_system_timing"]["sample_count"] == 1
    assert bpy.context.scene.frame_current == 1

    delivery = handler.realize_procedural_output(
        source.name,
        "Advanced Geometry Nodes Delivery",
        collection_name,
        delivery_mode="REALIZED_MESH",
    )
    assert delivery["source_retained"]
    assert bpy.data.objects[delivery["output_object"]].type == "MESH"
    assert bpy.data.objects.get(source.name) is source

    deleted = handler.manage_geometry_nodes_bake(
        source.name,
        "Advanced Geometry Nodes",
        action="DELETE",
        bake_id=bake_id,
        confirm_delete=True,
    )
    assert "FINISHED" in deleted["operator_result"]
    print("GEOMETRY_NODES_ADVANCED_SMOKE_OK")


if __name__ == "__main__":
    main()
