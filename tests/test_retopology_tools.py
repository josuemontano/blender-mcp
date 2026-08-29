"""Server-layer regression coverage for the retopology command surface."""

import asyncio

import pytest

from blender_mcp.server.tools import mesh, retopology, retopology_phase1
from blender_mcp.server.tools._envelope import STALE_INDEX_WARNING

RETOPOLOGY_TOOL_NAMES = {
    "create_retopology_target",
    "inspect_retopology",
    "analyze_surface_conformity",
    "manage_retopology_checkpoint",
    "configure_surface_projection",
    "project_mesh_elements",
    "build_quad_patch",
    "extend_boundary",
    "mesh_bridge",
    "fill_boundary_quads",
    "reroute_topology",
    "relax_topology",
    "redistribute_edge_loop",
    "configure_retopology_symmetry",
    "validate_retopology",
    "create_retopology_guides",
    "create_surface_section",
    "set_retopology_features",
    "add_support_loops",
    "transfer_mesh_attributes",
    "unwrap_retopology_uvs",
    "create_bake_cage",
    "bake_retopology_maps",
    "test_deformation",
}


class StubConnection:
    def __init__(self, result=None) -> None:
        self.result = result or {"name": "Low", "topology_revision": "revision"}
        self.calls = []

    def send_command(self, command, params):
        self.calls.append((command, params))
        return self.result


def test_all_retopology_tools_are_registered() -> None:
    registered = set(retopology.mcp._tool_manager._tools)

    assert registered >= RETOPOLOGY_TOOL_NAMES


@pytest.mark.parametrize(
    "tool,command,kwargs",
    [
        (retopology.inspect_retopology, "inspect_retopology", {"object_name": "Low"}),
        (
            retopology.analyze_surface_conformity,
            "analyze_surface_conformity",
            {"object_name": "Low", "source_object_name": "High"},
        ),
        (
            retopology.project_mesh_elements,
            "project_mesh_elements",
            {"object_name": "Low", "source_object_name": "High", "vertex_indices": [0]},
        ),
        (
            retopology.reroute_topology,
            "reroute_topology",
            {"object_name": "Low", "action": "SPLIT", "edge_indices": [0]},
        ),
        (
            retopology.validate_retopology,
            "validate_retopology",
            {"object_name": "Low"},
        ),
    ],
)
def test_retopology_tools_forward_without_context(monkeypatch, tool, command, kwargs) -> None:
    connection = StubConnection()
    monkeypatch.setattr(retopology, "get_blender_connection", lambda: connection)

    result = asyncio.run(tool(ctx=None, **kwargs))

    assert result["ok"] is True
    assert connection.calls[0][0] == command
    assert "ctx" not in connection.calls[0][1]


def test_configure_projection_forwards_exact_modifier_controls(monkeypatch) -> None:
    connection = StubConnection()
    monkeypatch.setattr(retopology, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        retopology.configure_surface_projection(
            ctx=None,
            object_name="Low",
            target_object_name="High",
            wrap_method="PROJECT",
            project_axes=(True, False, False),
            positive_direction=False,
            negative_direction=True,
        )
    )

    _, params = connection.calls[0]
    assert params["project_axes"] == (True, False, False)
    assert params["negative_direction"] is True
    assert result["changed_objects"] == ["Low"]


def test_topology_builders_warn_that_indices_are_stale(monkeypatch) -> None:
    connection = StubConnection()
    monkeypatch.setattr(retopology, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        retopology.build_quad_patch(
            ctx=None,
            object_name="Low",
            corners=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            u_segments=2,
            v_segments=2,
        )
    )

    assert result["warnings"] == [STALE_INDEX_WARNING]


def test_mesh_bridge_sends_separate_loop_and_revision_inputs(monkeypatch) -> None:
    connection = StubConnection()
    monkeypatch.setattr(mesh, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        mesh.mesh_bridge(
            ctx=None,
            object_name="Low",
            loop_a_edge_indices=[0, 1, 2, 3],
            loop_b_edge_indices=[4, 5, 6, 7],
            cuts=2,
            interpolation="SURFACE",
            smoothness=0.5,
            twist_offset=1,
            expected_revision="before",
        )
    )

    command, params = connection.calls[0]
    assert command == "mesh_bridge"
    assert params["loop_a_edge_indices"] == [0, 1, 2, 3]
    assert params["loop_b_edge_indices"] == [4, 5, 6, 7]
    assert params["expected_revision"] == "before"
    assert result["warnings"] == [STALE_INDEX_WARNING]


def test_checkpoint_create_reports_hidden_backup_as_changed_object(monkeypatch) -> None:
    connection = StubConnection({"name": "Low", "backup_object": "Low__checkpoint__before"})
    monkeypatch.setattr(retopology, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        retopology.manage_retopology_checkpoint(
            ctx=None,
            action="CREATE",
            object_name="Low",
            checkpoint_name="before",
        )
    )

    assert result["changed_objects"] == ["Low__checkpoint__before"]


@pytest.mark.parametrize(
    "tool,command,kwargs",
    [
        (
            retopology_phase1.set_retopology_features,
            "set_retopology_features",
            {"object_name": "Low", "edge_indices": [1], "sharp": True},
        ),
        (
            retopology_phase1.transfer_mesh_attributes,
            "transfer_mesh_attributes",
            {"source_object_name": "High", "object_name": "Low", "data_types": ["UVS"]},
        ),
        (
            retopology_phase1.unwrap_retopology_uvs,
            "unwrap_retopology_uvs",
            {"object_name": "Low"},
        ),
        (
            retopology_phase1.test_deformation,
            "test_deformation",
            {"object_name": "Low", "frames": [1, 10]},
        ),
    ],
)
def test_phase_one_tools_forward_agent_inputs(monkeypatch, tool, command, kwargs) -> None:
    connection = StubConnection()
    monkeypatch.setattr(retopology_phase1, "get_blender_connection", lambda: connection)

    result = asyncio.run(tool(ctx=None, **kwargs))

    assert result["ok"] is True
    assert len(connection.calls) == 1
    assert connection.calls[0][0] == command
    assert "ctx" not in connection.calls[0][1]


def test_create_guides_reports_actual_collision_safe_names(monkeypatch) -> None:
    connection = StubConnection(
        {"created_guide_objects": ["EyeGuide", "EyeGuide.001"], "guides": [], "coordinate_space": "WORLD"}
    )
    monkeypatch.setattr(retopology_phase1, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        retopology_phase1.create_retopology_guides(
            ctx=None,
            source_object_name="High",
            guides=[{"name": "EyeGuide", "role": "EYE_LOOP", "source_vertex_indices": [0, 1]}],
        )
    )

    assert result["changed_objects"] == ["EyeGuide", "EyeGuide.001"]


def test_support_loops_warn_that_topology_indices_are_stale(monkeypatch) -> None:
    connection = StubConnection()
    monkeypatch.setattr(retopology_phase1, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        retopology_phase1.add_support_loops(
            ctx=None,
            object_name="Low",
            edge_indices=[0, 1],
            width=0.1,
            expected_revision="before",
        )
    )

    assert result["warnings"] == [STALE_INDEX_WARNING]
    assert connection.calls[0][1]["expected_revision"] == "before"


def test_bake_reports_image_as_changed_resource(monkeypatch) -> None:
    connection = StubConnection({"image": "Low_NORMAL", "output_path": "/tmp/normal.exr"})
    monkeypatch.setattr(retopology_phase1, "get_blender_connection", lambda: connection)

    result = asyncio.run(
        retopology_phase1.bake_retopology_maps(
            ctx=None,
            object_name="Low",
            high_poly_object_names=["High"],
            map_type="NORMAL",
            output_path="/tmp/normal.exr",
            confirm=True,
        )
    )

    assert result["changed_resources"] == ["Low_NORMAL"]
