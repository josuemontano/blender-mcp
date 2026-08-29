"""Agent-facing guided retopology and asset-handoff tools."""

import logging

from typing import Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import STALE_INDEX_WARNING, ok

logger = logging.getLogger("BlenderMCPServer")


def _call(command: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return get_blender_connection().send_command(command, params)
    except Exception as exc:
        logger.error("%s failed: %s", command, exc)
        raise ToolError(f"{command} failed: {exc}") from exc


@mcp.tool()
async def create_retopology_guides(
    ctx: Context,
    source_object_name: str,
    guides: list[dict[str, Any]],
    collection_name: str = "Retopology Guides",
    projection_offset: float = 0.0,
    max_projection_distance: float | None = None,
) -> dict:
    """Create explicit world-space curve guides projected onto an evaluated source.

    Each guide dict must contain `name`, `role`, and exactly one of `points`
    or `source_vertex_indices`; it may contain `cyclic` (default false).
    `points` is an ordered list of world-space XYZ triples. Source indices
    refer to the source's current base mesh and are converted to world space
    before projection. Roles are caller-declared EYE_LOOP, MOUTH_LOOP,
    JOINT_RING, HARD_EDGE, SEAM, DENSITY_TRANSITION, PANEL_BOUNDARY, or CUSTOM;
    the tool never infers anatomy. Every input and projection is validated
    before any Curve object is created. Returns the projected world-space
    points and collision-safe object names.
    """
    result = _call(
        "create_retopology_guides",
        {
            "source_object_name": source_object_name,
            "guides": guides,
            "collection_name": collection_name,
            "projection_offset": projection_offset,
            "max_projection_distance": max_projection_distance,
        },
    )
    return ok(result, changed_objects=result["created_guide_objects"])


@mcp.tool()
async def create_surface_section(
    ctx: Context,
    source_object_name: str,
    plane_origin: tuple[float, float, float],
    plane_normal: tuple[float, float, float],
    vertex_count: int,
    name: str | None = None,
    collection_name: str = "Retopology Guides",
    component_index: int = 0,
    cyclic: bool = True,
    projection_offset: float = 0.0,
) -> dict:
    """Create a resampled guide from a world-space plane/source intersection.

    The evaluated source (including live modifiers) is intersected without
    modifying it. Connected sections are sorted by descending world-space
    length; `component_index=0` selects the longest. The selected polyline is
    resampled to exactly `vertex_count` points, reprojected to the evaluated
    source, and stored as a named Curve in `collection_name`. Use `cyclic=True`
    for a closed limb/pipe section and false only when an open intersection is
    intentional. The result lists every discovered component before selection.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    result = _call("create_surface_section", params)
    return ok(result, changed_objects=[result["guide_object"]])


@mcp.tool()
async def set_retopology_features(
    ctx: Context,
    object_name: str,
    edge_indices: list[int] | None = None,
    detect_source_object_name: str | None = None,
    source_dihedral_angle: float | None = None,
    include_material_boundaries: bool = False,
    guide_object_names: list[str] | None = None,
    guide_distance: float = 0.01,
    apply_detected: bool = False,
    seam: bool | None = None,
    sharp: bool | None = None,
    crease: float | None = None,
    bevel_weight: float | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Detect and optionally write coherent feature marks on explicit target edges.

    Explicit `edge_indices` are always active. Optional source dihedral,
    material-boundary, and guide proximity rules add *candidates*; candidates
    are only changed when `apply_detected=True`, so detection never silently
    activates every suggestion. Angles are radians in [0, pi], guide distances
    are world-space, and crease/bevel weights are in [0, 1]. `None` leaves a
    mark unchanged; booleans can set or clear seam/sharp. Blender 5.1 edge
    attributes `sharp_edge`, `crease_edge`, and `bevel_weight_edge` are used.
    All indices and `expected_revision` are validated before mutation. This
    changes attributes but not connectivity, so valid element indices remain stable.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("set_retopology_features", params), changed_objects=[object_name])


@mcp.tool()
async def add_support_loops(
    ctx: Context,
    object_name: str,
    edge_indices: list[int],
    width: float,
    side: Literal["BOTH", "LEFT", "RIGHT"] = "BOTH",
    clamp: bool = True,
    corner_policy: Literal["MITER", "CAP_ENDPOINTS"] = "MITER",
    source_object_name: str | None = None,
    projection_offset: float = 0.0,
    subdivision_levels: int = 2,
    expected_revision: str | None = None,
) -> dict:
    """Insert deterministic support loops around selected manifold feature edges.

    The ordered-independent edge IDs must describe non-branching manifold
    chains. `width` is Blender's positive Edge Slide factor (0, 10]. BOTH runs
    equal offsets on both sides; LEFT or RIGHT retains only that signed side.
    `clamp` prevents overshoot and CAP_ENDPOINTS extends around open ends.
    Blender's verified Offset Edge Loops + Edge Slide operator is used in a
    restored Edit-Mode context; cancellation is an error. New vertices are
    optionally projected to the evaluated source. A live Subdivision Surface
    modifier is created or updated, never applied. Returns created element IDs,
    manifold validation, modifier order, and a new topology revision.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(
        _call("add_support_loops", params),
        changed_objects=[object_name],
        warnings=[STALE_INDEX_WARNING],
    )


@mcp.tool()
async def transfer_mesh_attributes(
    ctx: Context,
    source_object_name: str,
    object_name: str,
    data_types: list[
        Literal[
            "VERTEX_GROUPS",
            "UVS",
            "COLOR_ATTRIBUTES",
            "CUSTOM_NORMALS",
            "SEAMS",
            "CREASES",
            "BEVEL_WEIGHTS",
            "SHARP_EDGES",
            "SMOOTH_SHADING",
            "MATERIAL_INDICES",
        ]
    ],
    modifier_name: str = "RetopologyDataTransfer",
    vertex_mapping: str = "POLYINTERP_NEAREST",
    edge_mapping: str = "NEAREST",
    loop_mapping: str = "POLYINTERP_NEAREST",
    polygon_mapping: str = "NEAREST",
    use_object_transform: bool = True,
    max_distance: float | None = None,
    source_layers: Literal["ACTIVE", "ALL"] = "ALL",
    destination_layers: Literal["NAME", "INDEX"] = "NAME",
    mix_mode: str = "REPLACE",
    mix_factor: float = 1.0,
    apply: bool = False,
) -> dict:
    """Transfer named production data from a source mesh to new topology.

    Data Transfer supports vertex groups, UVs, colors, custom normals, seams,
    creases, bevel weights, sharp edges, and smooth shading. Mapping enum
    strings are passed to the corresponding Blender 5.1 vertex/edge/loop/face
    mapping property and invalid values are rejected by RNA before commit.
    `use_object_transform=True` evaluates mapping in world space. `max_distance=None`
    disables the distance gate. The named modifier stays live by default;
    `apply=True` bakes all supported data types; connectivity and indices are retained.
    MATERIAL_INDICES is always mapped immediately from nearest evaluated source
    faces because Data Transfer does not support it; source material slots are
    added to the destination without deleting existing slots.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    result = _call("transfer_mesh_attributes", params)
    return ok(result, changed_objects=[object_name])


@mcp.tool()
async def unwrap_retopology_uvs(
    ctx: Context,
    object_name: str,
    uv_map_name: str = "RetopologyUV",
    method: Literal["ANGLE_BASED", "CONFORMAL", "MINIMUM_STRETCH"] = "ANGLE_BASED",
    replace_existing: bool = False,
    average_island_scale: bool = True,
    minimize_stretch_iterations: int = 10,
    pack_islands: bool = True,
    margin: float = 0.001,
) -> dict:
    """Create and validate a seam-driven bake-ready UV map.

    All base-mesh faces are unwrapped using existing seam attributes; the tool
    does not invent seams. An existing map with `uv_map_name` is preserved and
    causes an error unless `replace_existing=True`. Optional island scaling,
    bounded stretch minimization, and packing are checked for FINISHED status.
    Returns island count, zero-area faces, overlap pairs, UVs outside [0,1],
    geometric/UV stretch statistics, and texel-density variation. Existing
    maps other than an explicitly replaced same-name map are untouched.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("unwrap_retopology_uvs", params), changed_objects=[object_name])


@mcp.tool()
async def create_bake_cage(
    ctx: Context,
    object_name: str,
    high_poly_object_names: list[str],
    name: str | None = None,
    collection_name: str = "Retopology Bake Cages",
    offset: float = 0.02,
    vertex_group: str | None = None,
    validate_enclosure: bool = True,
) -> dict:
    """Create an editable, non-rendering cage with low-poly-identical topology.

    The base mesh is copied without modifiers, keeping vertex/edge/face indices
    identical. Cage vertices move in target-local averaged-normal directions by
    `offset`, multiplied by `vertex_group` weights when supplied. The cage uses
    the low-poly world transform, is placed in a dedicated collection, remains
    viewport-visible, and is hidden from renders. Validation reports topology
    identity, self-intersections, high-poly samples likely outside the cage,
    and bidirectional normal-ray misses; it does not silently alter the cage.
    """
    result = _call(
        "create_bake_cage",
        {
            "object_name": object_name,
            "high_poly_object_names": high_poly_object_names,
            "name": name,
            "collection_name": collection_name,
            "offset": offset,
            "vertex_group": vertex_group,
            "validate_enclosure": validate_enclosure,
        },
    )
    return ok(result, changed_objects=[result["cage_object"]])


@mcp.tool()
async def bake_retopology_maps(
    ctx: Context,
    object_name: str,
    high_poly_object_names: list[str],
    map_type: Literal["NORMAL", "DISPLACEMENT", "AO", "POSITION", "DIFFUSE", "ROUGHNESS", "EMISSION"],
    output_path: str,
    width: int = 2048,
    height: int = 2048,
    uv_map_name: str | None = None,
    cage_object_name: str | None = None,
    cage_extrusion: float = 0.0,
    max_ray_distance: float = 0.0,
    margin: int = 16,
    normal_space: Literal["TANGENT", "OBJECT"] = "TANGENT",
    normal_swizzle: tuple[str, str, str] = ("POS_X", "POS_Y", "POS_Z"),
    overwrite: bool = False,
    confirm: bool = False,
) -> dict:
    """Bake one validated high-to-low map to an explicit file path.

    This is synchronous and potentially expensive, so `confirm=True` is
    required. The absolute `output_path` parent must exist; an existing file is
    rejected unless `overwrite=True`. The low mesh needs non-empty UVs and is
    selected-active while every named high mesh is selected as a source.
    Cycles, selection, active object, active UV, and active material image nodes
    are restored in `finally`. A supplied cage must have topology identical to
    the low mesh. `cage_extrusion` and `max_ray_distance` are world-space scene
    units; normal channel values use Blender's POS_X/NEG_X etc. enums. Returns
    image name, dimensions, map type, and the written path only after bake and save succeed.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    result = _call("bake_retopology_maps", params)
    return ok(result, changed_objects=[object_name], changed_resources=[result["image"]])


@mcp.tool()
async def test_deformation(
    ctx: Context,
    object_name: str,
    frames: list[int],
    reference_frame: int | None = None,
    source_object_name: str | None = None,
    joint_vertex_groups: list[str] | None = None,
    stretch_warning_ratio: float = 1.25,
    area_warning_ratio: float = 1.5,
    check_self_intersections: bool = True,
    issue_limit: int = 100,
) -> dict:
    """Evaluate deformation quality at explicit animation frames without editing it.

    The current scene frame is restored in `finally` and no keyframes are
    inserted. Evaluated meshes include the live modifier/armature stack.
    Every requested frame is compared with `reference_frame` (or the current
    frame): edge stretch/compression ratios, face-area ratios, signed volume
    change, flipped face normals, and non-adjacent self-intersections are
    reported. `joint_vertex_groups` restricts detailed worst-element lists to
    caller-declared joint regions while whole-mesh summaries remain available.
    When `source_object_name` is supplied, world-space conformity to that
    evaluated animated source is also measured for joint vertices (or every
    vertex when no joint groups are supplied). This inspection changes no datablocks.
    """
    params = {key: value for key, value in locals().items() if key != "ctx"}
    return ok(_call("test_deformation", params))
