"""Agent-facing retopology tools.

The docstrings in this module are deliberately operational: they tell an MCP
agent which indices are accepted, which coordinate space is used, and when a
fresh topology revision is required.
"""

import logging

from typing import Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import STALE_INDEX_WARNING, ok

logger = logging.getLogger("BlenderMCPServer")

InitialGeometry = Literal["EMPTY", "SINGLE_VERTEX", "PLANE", "GRID", "DUPLICATED_EVALUATED_SURFACE"]
ProjectionMethod = Literal["NEAREST", "RAYCAST"]
CheckpointAction = Literal["CREATE", "LIST", "COMPARE", "RESTORE", "DELETE"]
RerouteAction = Literal["CONNECT", "ROTATE_DIAGONAL", "COLLAPSE", "DISSOLVE", "SPLIT"]


def _call(command: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return get_blender_connection().send_command(command, params)
    except Exception as exc:
        logger.error("%s failed: %s", command, exc)
        raise ToolError(f"{command} failed: {exc}") from exc


@mcp.tool()
async def create_retopology_target(
    ctx: Context,
    source_object_names: list[str],
    name: str | None = None,
    initial_geometry: InitialGeometry = "EMPTY",
    collection_name: str = "Retopology",
    size: float = 1.0,
    grid_segments: tuple[int, int] = (4, 4),
    add_mirror: bool = False,
    add_shrinkwrap: bool = True,
    subdivision_levels: int = 0,
) -> dict:
    """Create an editable low-poly target linked to one or more source meshes.

    Use EMPTY when topology will be built by later tools, SINGLE_VERTEX for
    vertex-by-vertex work, PLANE/GRID for a starter patch, or
    DUPLICATED_EVALUATED_SURFACE to copy the sources' currently evaluated
    geometry. The target is put in `collection_name`, receives the first
    source's world transform, and records all source names in custom
    properties. Mirror and Shrinkwrap remain live and are ordered before an
    optional Subdivision Surface modifier.

    Args:
        ctx: MCP request context.
        source_object_names: Existing mesh objects, ordered with the primary projection source first.
        name: Desired target object name; Blender makes it collision-safe when already used.
        initial_geometry: Starter geometry strategy.
        collection_name: Dedicated collection to create or reuse.
        size: Local width/height for PLANE and GRID; must be positive.
        grid_segments: Vertex counts [u, v] for GRID, each at least 2.
        add_mirror: Add a live X-axis Mirror modifier.
        add_shrinkwrap: Add a live named Shrinkwrap targeting the first source.
        subdivision_levels: Live Subdivision Surface viewport/render levels, 0 to 6; 0 omits it.

    Returns:
        Target name, source links, collection, base counts, modifier order, and topology revision.
    """
    result = _call(
        "create_retopology_target",
        {
            "source_object_names": source_object_names,
            "name": name,
            "initial_geometry": initial_geometry,
            "collection_name": collection_name,
            "size": size,
            "grid_segments": list(grid_segments),
            "add_mirror": add_mirror,
            "add_shrinkwrap": add_shrinkwrap,
            "subdivision_levels": subdivision_levels,
        },
    )
    return ok(result, changed_objects=[result["name"]])


@mcp.tool()
async def inspect_retopology(
    ctx: Context,
    object_name: str,
    selected_vertex_indices: list[int] | None = None,
    adjacency_depth: int = 1,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Inspect topology quality and a bounded set of planning details.

    This is the preferred inspection before index-based retopology edits. It
    reports aggregate components, boundary-loop summaries, face-type counts,
    non-manifold edges, poles grouped by valence, isolated/degenerate
    elements, edge-length and face-aspect statistics, UV layers, vertex
    groups, modifiers, and symmetry evidence. When vertices are supplied, it
    also returns their adjacency neighborhoods up to `adjacency_depth`.
    Potentially long diagnostic element lists share one deterministic
    `offset`/`limit` page; `boundary_vertex` records include loop/order fields
    for reconstructing ordered loops. Reuse the returned `topology_revision` as
    `expected_revision` in mutating tools so stale indices are rejected.
    Coordinates and lengths are base-mesh local space unless explicitly named world space.
    """
    return ok(
        _call(
            "inspect_retopology",
            {
                "object_name": object_name,
                "selected_vertex_indices": selected_vertex_indices,
                "adjacency_depth": adjacency_depth,
                "limit": limit,
                "offset": offset,
            },
        )
    )


@mcp.tool()
async def analyze_surface_conformity(
    ctx: Context,
    object_name: str,
    source_object_name: str,
    sample_vertices: bool = True,
    sample_edge_midpoints: bool = False,
    sample_face_centroids: bool = False,
    max_distance: float | None = None,
    worst_limit: int = 20,
    create_heat_map: bool = False,
    attribute_name: str = "retopology_distance",
) -> dict:
    """Measure world-space distance from a target to an evaluated source.

    The source BVH includes its live modifiers. Results include mean, RMS,
    p50/p90/p95/p99, maximum, signed offsets where the nearest triangle normal
    gives a reliable orientation, missed samples, and the worst element IDs.
    Enable edge/face samples when sparse target vertices could hide poor
    conformity. A POINT/FLOAT heat-map attribute is created only when
    `create_heat_map=True`; its values always represent vertex samples.
    """
    result = _call(
        "analyze_surface_conformity",
        {
            "object_name": object_name,
            "source_object_name": source_object_name,
            "sample_vertices": sample_vertices,
            "sample_edge_midpoints": sample_edge_midpoints,
            "sample_face_centroids": sample_face_centroids,
            "max_distance": max_distance,
            "worst_limit": worst_limit,
            "create_heat_map": create_heat_map,
            "attribute_name": attribute_name,
        },
    )
    return ok(result, changed_objects=[object_name] if create_heat_map else [])


@mcp.tool()
async def manage_retopology_checkpoint(
    ctx: Context,
    action: CheckpointAction,
    object_name: str,
    checkpoint_name: str | None = None,
    confirm: bool = False,
) -> dict:
    """Create, list, compare, restore, or delete recoverable mesh checkpoints.

    CREATE copies the mesh, local transform, modifier settings, vertex groups,
    and custom attributes into a hidden backup collection. LIST needs only the
    target name. COMPARE reports count/hash differences without mutation.
    RESTORE and DELETE require `confirm=True`; RESTORE replaces the target's
    mesh and relevant object state while keeping the checkpoint available.
    Provide `checkpoint_name` for every action except LIST.
    """
    result = _call(
        "manage_retopology_checkpoint",
        {"action": action, "object_name": object_name, "checkpoint_name": checkpoint_name, "confirm": confirm},
    )
    normalized_action = action.upper()
    if normalized_action == "RESTORE":
        changed = [object_name]
    elif normalized_action in {"CREATE", "DELETE"} and result.get("backup_object"):
        changed = [result["backup_object"]]
    else:
        changed = []
    return ok(result, changed_objects=changed)


@mcp.tool()
async def configure_surface_projection(
    ctx: Context,
    object_name: str,
    target_object_name: str,
    modifier_name: str = "RetopologyProjection",
    wrap_method: Literal[
        "NEAREST_SURFACEPOINT", "PROJECT", "NEAREST_VERTEX", "TARGET_PROJECT"
    ] = "NEAREST_SURFACEPOINT",
    wrap_mode: Literal["ON_SURFACE", "INSIDE", "OUTSIDE", "OUTSIDE_SURFACE", "ABOVE_SURFACE"] = "ON_SURFACE",
    offset: float = 0.0,
    project_limit: float = 0.0,
    project_axes: tuple[bool, bool, bool] = (False, False, True),
    positive_direction: bool = True,
    negative_direction: bool = True,
    cull_face: Literal["OFF", "FRONT", "BACK"] = "OFF",
    invert_cull: bool = False,
    auxiliary_target_name: str | None = None,
    vertex_group: str = "",
    invert_vertex_group: bool = False,
    apply: bool = False,
) -> dict:
    """Idempotently create or update one named live Shrinkwrap relationship.

    Recalling with the same `modifier_name` updates that modifier instead of
    stacking another. PROJECT uses the selected local projection axes and
    direction flags; `project_limit=0` means unlimited. A non-empty
    `vertex_group` must already exist. The exact post-call modifier order is
    returned. The modifier stays live unless `apply=True`, which bakes its
    evaluated result into the base mesh and invalidates prior indices.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    result = _call("configure_surface_projection", params)
    warnings = [STALE_INDEX_WARNING] if apply else []
    return ok(result, changed_objects=[object_name], warnings=warnings)


@mcp.tool()
async def project_mesh_elements(
    ctx: Context,
    object_name: str,
    source_object_name: str,
    vertex_indices: list[int] | None = None,
    vertex_group: str | None = None,
    method: ProjectionMethod = "NEAREST",
    direction: tuple[float, float, float] = (0.0, 0.0, -1.0),
    direction_space: Literal["LOCAL", "WORLD"] = "WORLD",
    offset: float = 0.0,
    max_distance: float | None = None,
    positive_direction: bool = True,
    negative_direction: bool = False,
    backface_policy: Literal["ALLOW", "CULL"] = "ALLOW",
    preserve_boundary: bool = False,
    preserve_symmetry_axis: Literal["NONE", "X", "Y", "Z"] = "NONE",
    symmetry_tolerance: float = 0.0001,
    expected_revision: str | None = None,
) -> dict:
    """Project explicit target vertices onto an evaluated source without snapping.

    Supply exactly one of `vertex_indices` or `vertex_group`. NEAREST uses the
    closest BVH point. RAYCAST tries the requested positive and/or negative
    direction and chooses the nearest acceptable hit. Direction vectors are
    interpreted in `direction_space`; positions and distances are always
    processed in world space, then written back to target-local coordinates.
    All indices and revision are validated before mutation. Boundary or
    symmetry-plane vertices can be retained. Failed vertex IDs are reported.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("project_mesh_elements", params), changed_objects=[object_name])


@mcp.tool()
async def build_quad_patch(
    ctx: Context,
    object_name: str,
    corners: list[tuple[float, float, float]],
    u_segments: int,
    v_segments: int,
    source_object_name: str | None = None,
    coordinate_space: Literal["LOCAL", "WORLD"] = "WORLD",
    interpolation: Literal["BILINEAR", "COONS"] = "BILINEAR",
    boundary_u0: list[tuple[float, float, float]] | None = None,
    boundary_u1: list[tuple[float, float, float]] | None = None,
    boundary_v0: list[tuple[float, float, float]] | None = None,
    boundary_v1: list[tuple[float, float, float]] | None = None,
    projection_offset: float = 0.0,
    expected_revision: str | None = None,
) -> dict:
    """Append a regular quad grid defined by four ordered corners or Coons guides.

    Corners are ordered [u0v0, u1v0, u1v1, u0v1]. `u_segments` and
    `v_segments` are face counts, each at least 1. COONS additionally requires
    all four boundary polylines; their endpoints must match the corners and
    opposite guides must not cross. New vertices are optionally projected to
    the evaluated source. Returns every created vertex/edge/face index and a
    fresh topology revision.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("build_quad_patch", params), changed_objects=[object_name], warnings=[STALE_INDEX_WARNING])


@mcp.tool()
async def extend_boundary(
    ctx: Context,
    object_name: str,
    ordered_boundary_vertex_indices: list[int],
    rows: int = 1,
    distance: float = 0.1,
    mode: Literal["FIXED_VECTOR", "VERTEX_NORMAL", "GUIDE_DIRECTED", "SURFACE_TANGENT"] = "VERTEX_NORMAL",
    vector: tuple[float, float, float] = (0.0, 0.0, 1.0),
    guide_points: list[tuple[float, float, float]] | None = None,
    source_object_name: str | None = None,
    projection_offset: float = 0.0,
    expected_revision: str | None = None,
) -> dict:
    """Grow one or more quad rows from one ordered open manifold boundary.

    The supplied vertices must form one non-branching open boundary in order;
    no implicit sorting or selection is used. FIXED_VECTOR uses `vector` in
    target-local space, VERTEX_NORMAL uses averaged local boundary normals,
    GUIDE_DIRECTED follows a same-length world-space guide polyline, and
    SURFACE_TANGENT removes the source-normal component from `vector`. New
    rows are projected when a source is supplied. Returns all created IDs and
    the new topology revision.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("extend_boundary", params), changed_objects=[object_name], warnings=[STALE_INDEX_WARNING])


@mcp.tool()
async def fill_boundary_quads(
    ctx: Context,
    object_name: str,
    boundary_edge_indices: list[int],
    span: int = 1,
    offset: int = 0,
    use_interp_simple: bool = False,
    source_object_name: str | None = None,
    projection_offset: float = 0.0,
    expected_revision: str | None = None,
) -> dict:
    """Fill a compatible closed hole boundary with quads only.

    `boundary_edge_indices` must be exactly one ordered-compatible closed
    manifold boundary with an even vertex count. `span` and `offset` select
    the grid correspondence, matching Blender's Grid Fill controls. The tool
    rejects any result containing triangles or n-gons and never falls back to
    a generic fill. New vertices may be projected to an evaluated source.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("fill_boundary_quads", params), changed_objects=[object_name], warnings=[STALE_INDEX_WARNING])


@mcp.tool()
async def reroute_topology(
    ctx: Context,
    object_name: str,
    action: RerouteAction,
    vertex_indices: list[int] | None = None,
    edge_indices: list[int] | None = None,
    cuts: int = 1,
    expected_revision: str | None = None,
) -> dict:
    """Perform one bounded local edge-flow correction.

    CONNECT needs two vertices, ROTATE_DIAGONAL one interior edge shared by
    two triangles, COLLAPSE one or more edges, DISSOLVE one or more edges, and
    SPLIT one or more edges plus `cuts`. Inputs are prevalidated on a copied
    BMesh; results that introduce non-manifold edges, duplicate faces, or new
    unintended boundaries are rejected before the real mesh is changed.
    Returns created/removed element IDs where stable plus a new revision.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("reroute_topology", params), changed_objects=[object_name], warnings=[STALE_INDEX_WARNING])


@mcp.tool()
async def relax_topology(
    ctx: Context,
    object_name: str,
    vertex_indices: list[int],
    iterations: int = 3,
    factor: float = 0.5,
    lock_boundary: bool = True,
    lock_vertex_group: str | None = None,
    source_object_name: str | None = None,
    projection_offset: float = 0.0,
    expected_revision: str | None = None,
) -> dict:
    """Tangentially smooth a bounded patch while retaining its source shape.

    Each iteration computes adjacency-based Laplacian movement, removes its
    component along the current vertex normal, applies `factor` in [0, 1],
    and optionally reprojects to the evaluated source. Boundary vertices and
    vertices with non-zero weight in `lock_vertex_group` can be fixed. This
    native implementation does not require LoopTools and does not change
    topology, so indices remain valid when the revision matches.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("relax_topology", params), changed_objects=[object_name])


@mcp.tool()
async def redistribute_edge_loop(
    ctx: Context,
    object_name: str,
    loop_vertex_indices: list[int],
    closed: bool = False,
    preserve_endpoints: bool = True,
    corner_vertex_indices: list[int] | None = None,
    source_object_name: str | None = None,
    projection_offset: float = 0.0,
    expected_revision: str | None = None,
) -> dict:
    """Evenly redistribute an explicitly ordered open or closed edge loop.

    The supplied order must follow existing edges exactly. Positions are
    resampled by cumulative target-local arc length, independently between
    protected corners. Open endpoints remain fixed by default. Optional
    source reprojection keeps the redistributed loop on the evaluated shape.
    This changes positions only, not topology.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("redistribute_edge_loop", params), changed_objects=[object_name])


@mcp.tool()
async def configure_retopology_symmetry(
    ctx: Context,
    object_name: str,
    axis: Literal["X", "Y", "Z"] = "X",
    mirror_object_name: str | None = None,
    source_side: Literal["POSITIVE", "NEGATIVE"] = "POSITIVE",
    bisect: bool = False,
    clipping: bool = True,
    merge: bool = True,
    merge_tolerance: float = 0.001,
    mirror_vertex_groups: bool = True,
    validate_seam: bool = True,
    symmetry_tolerance: float = 0.001,
    modifier_name: str = "RetopologyMirror",
) -> dict:
    """Idempotently configure and validate a live retopology Mirror modifier.

    The plane is the target's local origin unless `mirror_object_name` is
    supplied. `source_side` controls which half survives when `bisect=True`.
    Clipping and merge remain live; the modifier is never applied by this
    tool. KD-tree matching reports unmatched mirrored base vertices and seam
    vertices outside `symmetry_tolerance`, so an agent can repair center-seam
    damage before continuing. The exact modifier order is returned.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("configure_retopology_symmetry", params), changed_objects=[object_name])


@mcp.tool()
async def validate_retopology(
    ctx: Context,
    object_name: str,
    profile: Literal["CHARACTER", "HARD_SURFACE", "VFX", "GAME"] = "CHARACTER",
    source_object_name: str | None = None,
    thresholds: dict[str, float] | None = None,
    check_self_intersections: bool = True,
    check_uv_overlap: bool = True,
    check_skin_weights: bool = True,
    issue_limit: int = 100,
) -> dict:
    """Return a production pass/warn/fail report for a retopology profile.

    Profiles select documented thresholds rather than enforcing a blanket
    all-quads rule. Checks cover manifoldness and allowed boundaries, doubles,
    degenerates, winding, self-intersections, face aspect and density changes,
    poles, live symmetry, source conformity, UV presence/overlap, skin-weight
    normalization, and modifier readiness/order. `thresholds` may override
    named numeric profile values returned in the report. Mesh.validate() is
    run only on a temporary copy. Long issue lists are capped by `issue_limit`.
    The overall status is FAIL when any fail-severity check fails, WARN when
    only warnings remain, otherwise PASS.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("validate_retopology", params))
