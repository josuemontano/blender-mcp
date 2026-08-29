# MCP tool signatures intentionally expose more than five keyword arguments so
# agents receive precise JSON schemas instead of opaque catch-all dictionaries.
"""Typed tools for inspecting and configuring Blender cloth simulations."""

import asyncio
import logging

from typing import Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

MaterialPreset = Literal["COTTON", "SILK", "DENIM", "LEATHER", "RUBBER"]
ExistingPolicy = Literal["ERROR", "REUSE"]
WeightOperation = Literal["REPLACE", "ADD", "SUBTRACT"]
WeightRole = Literal[
    "PIN_MASS",
    "STRUCTURAL_STIFFNESS",
    "SHEAR_STIFFNESS",
    "BENDING_STIFFNESS",
    "SHRINK",
    "PRESSURE",
    "INTERNAL_SPRINGS",
    "OBJECT_COLLISION_EXCLUSION",
    "SELF_COLLISION_EXCLUSION",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClothMaterialPatch(_StrictModel):
    """Allowlisted Blender 5.1 cloth material properties."""

    mass: float | None = None
    air_damping: float | None = None
    bending_model: Literal["ANGULAR", "LINEAR"] | None = None
    tension_stiffness: float | None = None
    tension_stiffness_max: float | None = None
    compression_stiffness: float | None = None
    compression_stiffness_max: float | None = None
    shear_stiffness: float | None = None
    shear_stiffness_max: float | None = None
    bending_stiffness: float | None = None
    bending_stiffness_max: float | None = None
    tension_damping: float | None = None
    compression_damping: float | None = None
    shear_damping: float | None = None
    bending_damping: float | None = None


class ClothSolverPatch(_StrictModel):
    """Allowlisted solver controls, deliberately excluding material and collision settings."""

    quality: int | None = None
    time_scale: float | None = None
    gravity: tuple[float, float, float] | None = None
    voxel_cell_size: float | None = None


class ClothCollisionPatch(_StrictModel):
    """Allowlisted cloth-side object and self-collision controls."""

    use_collision: bool | None = None
    collision_quality: int | None = None
    distance_min: float | None = None
    impulse_clamp: float | None = None
    damping: float | None = None
    friction: float | None = None
    collection_name: str | None = None
    clear_collection: bool = False
    vertex_group_object_collisions: str | None = None
    use_self_collision: bool | None = None
    self_distance_min: float | None = None
    self_friction: float | None = None
    self_impulse_clamp: float | None = None
    vertex_group_self_collisions: str | None = None


class ClothPinningPatch(_StrictModel):
    """Pin goal controls applied to an existing vertex group."""

    pin_stiffness: float | None = None
    goal_min: float | None = None
    goal_max: float | None = None
    goal_default: float | None = None
    goal_spring: float | None = None
    goal_friction: float | None = None


class ClothColliderPatch(_StrictModel):
    """CollisionSettings fields documented as cloth-relevant in Blender 5.1."""

    use: bool | None = None
    thickness_outer: float | None = None
    cloth_friction: float | None = None
    damping: float | None = None
    use_culling: bool | None = None
    use_normal: bool | None = None


class VertexWeightAssignment(_StrictModel):
    """Assign one exact base-mesh vertex index a normalized weight."""

    vertex_index: int = Field(ge=0)
    weight: float = Field(ge=0.0, le=1.0)


class ClothColliderRegistration(_StrictModel):
    """Register one collider through an explicit cloth and collection relationship."""

    cloth_object_name: str
    cloth_modifier_name: str
    collection_name: str


def _dump(model: BaseModel | None) -> dict | None:
    return model.model_dump(exclude_none=True, exclude_unset=True) if model is not None else None


def _call(command: str, params: dict, changed_objects: list[str] | None = None) -> dict:
    try:
        result = get_blender_connection().send_command(command, params)
        changed = result.get("changed_objects", changed_objects or []) if isinstance(result, dict) else changed_objects
        if isinstance(result, dict) and "changed_objects" in result:
            result = {key: value for key, value in result.items() if key != "changed_objects"}
        return ok(result, changed_objects=changed or [])
    except Exception as exc:
        logger.error("Error running %s: %s", command, exc)
        raise ToolError(f"Error running {command}: {exc}") from exc


@mcp.tool()
async def get_cloth_simulation_info(
    ctx: Context,
    scene_name: str,
    collection_name: str | None = None,
    object_limit: int = 25,
    object_offset: int = 0,
    dependency_limit: int = 100,
    dependency_offset: int = 0,
) -> dict:
    """Inspect cloth systems in one explicit scene or collection without evaluating another frame.

    Use the two independent offsets to continue the object and dependency pages. Eligible-collider
    records distinguish active relationships from in-scope but disabled collision; objects excluded
    by a cloth collision collection are never reported as affecting that cloth.
    """
    return await asyncio.to_thread(
        _call,
        "get_cloth_simulation_info",
        {
            "scene_name": scene_name,
            "collection_name": collection_name,
            "object_limit": object_limit,
            "object_offset": object_offset,
            "dependency_limit": dependency_limit,
            "dependency_offset": dependency_offset,
        },
    )


@mcp.tool()
async def get_cloth_object_info(
    ctx: Context,
    object_name: str,
    vertex_group_limit: int = 50,
    vertex_group_offset: int = 0,
) -> dict:
    """Inspect one named cloth or collider before planning a mutation.

    Base mesh statistics and vertex indices are object-local; evaluated counts include the live
    dependency graph. Vertex groups are separately paginated and can change after topology edits.
    """
    return await asyncio.to_thread(
        _call,
        "get_cloth_object_info",
        {
            "object_name": object_name,
            "vertex_group_limit": vertex_group_limit,
            "vertex_group_offset": vertex_group_offset,
        },
    )


@mcp.tool()
async def add_cloth_simulation(
    ctx: Context,
    object_name: str,
    modifier_name: str = "Cloth",
    modifier_index: int | None = None,
    existing_policy: ExistingPolicy = "ERROR",
    cache_frame_start: int = 1,
    cache_frame_end: int = 250,
    collision_collection_name: str | None = None,
    preset: MaterialPreset | None = None,
    material: ClothMaterialPatch | None = None,
    solver: ClothSolverPatch | None = None,
    collisions: ClothCollisionPatch | None = None,
) -> dict:
    """Add a named live Cloth modifier to an explicit nonempty mesh, without baking or applying it.

    ``existing_policy='ERROR'`` is the safe default. ``REUSE`` targets only a same-name Cloth
    modifier and still refuses baked caches. All supplied patches and the cache range are validated;
    failure removes a newly created modifier or restores the reused modifier's touched properties.
    """
    return await asyncio.to_thread(
        _call,
        "add_cloth_simulation",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "modifier_index": modifier_index,
            "existing_policy": existing_policy,
            "cache_frame_start": cache_frame_start,
            "cache_frame_end": cache_frame_end,
            "collision_collection_name": collision_collection_name,
            "preset": preset,
            "material": _dump(material),
            "solver": _dump(solver),
            "collisions": _dump(collisions),
        },
        [object_name],
    )


@mcp.tool()
async def configure_cloth_material(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    patch: ClothMaterialPatch | None = None,
    preset: MaterialPreset | None = None,
) -> dict:
    """Patch cloth mass, stiffness, and damping as one material model.

    Presets reproduce Blender 5.1's shipped starting values and are not real-world calibration.
    Explicit patch fields override preset fields. A baked cache is never freed automatically; a
    successful edit invalidates the unbaked simulation state and returns exact old/new values.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_material",
        {"object_name": object_name, "modifier_name": modifier_name, "patch": _dump(patch), "preset": preset},
        [object_name],
    )


@mcp.tool()
async def configure_cloth_solver(ctx: Context, object_name: str, modifier_name: str, patch: ClothSolverPatch) -> dict:
    """Patch solver-only quality, timing, gravity, and voxel controls on one Cloth modifier.

    Material stiffness and collision quality belong to their dedicated tools. Baked caches are
    rejected and never freed. The returned cost multiplier is relative, not a solve-time promise.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_solver",
        {"object_name": object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [object_name],
    )


@mcp.tool()
async def set_cloth_vertex_weights(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    role: WeightRole,
    group_name: str,
    assignments: list[VertexWeightAssignment],
    operation: WeightOperation = "REPLACE",
) -> dict:
    """Create or update one role-specific cloth vertex group using exact base-mesh indices.

    The complete batch is validated before editing. ADD and SUBTRACT clamp to [0, 1]; unrelated and
    locked groups are preserved. Query mesh indices again after any topology-changing operation.
    """
    return await asyncio.to_thread(
        _call,
        "set_cloth_vertex_weights",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "role": role,
            "group_name": group_name,
            "assignments": [item.model_dump() for item in assignments],
            "operation": operation,
        },
        [object_name],
    )


@mcp.tool()
async def configure_cloth_pinning(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    group_name: str,
    patch: ClothPinningPatch,
) -> dict:
    """Assign an existing group as cloth pins and patch its goal behavior.

    This does not create weights or animation. Use set_cloth_vertex_weights first when necessary.
    The result reports weak/empty coverage and deformers that occur on the wrong side of Cloth.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_pinning",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "group_name": group_name,
            "patch": _dump(patch),
        },
        [object_name],
    )


@mcp.tool()
async def configure_cloth_collisions(
    ctx: Context, object_name: str, modifier_name: str, patch: ClothCollisionPatch
) -> dict:
    """Patch cloth-side object and self-collision, scope, and exclusion groups.

    ``collection_name`` selects one existing scene-linked collision collection;
    ``clear_collection`` restores scene-wide scope. The tool never adds Collision modifiers and
    rejects baked caches. Distances are compared with local edge scale and active collider thickness.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_collisions",
        {"object_name": object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [object_name],
    )


@mcp.tool()
async def add_cloth_collider(
    ctx: Context,
    object_name: str,
    modifier_name: str = "Collision",
    existing_policy: ExistingPolicy = "ERROR",
    settings: ClothColliderPatch | None = None,
    registrations: list[ClothColliderRegistration] | None = None,
) -> dict:
    """Add or explicitly reuse Collision physics on a named mesh or curve.

    Each registration links the collider into an existing collection and assigns that collection as
    the named cloth setup's collision scope; existing object collection links are preserved. Baked
    affected cloth caches are rejected, and request-created modifiers/links are rolled back on error.
    """
    return await asyncio.to_thread(
        _call,
        "add_cloth_collider",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "existing_policy": existing_policy,
            "settings": _dump(settings),
            "registrations": [item.model_dump() for item in registrations or []],
        },
        [object_name],
    )


@mcp.tool()
async def configure_cloth_collider(
    ctx: Context, object_name: str, modifier_name: str, patch: ClothColliderPatch
) -> dict:
    """Patch Blender 5.1 cloth-relevant collider thickness, friction, damping, and sidedness.

    This never adds rigid-body, fluid, particle, or soft-body behavior. It reports all active,
    in-scope cloth caches and refuses the edit if any of those caches is baked.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_collider",
        {"object_name": object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [object_name],
    )


@mcp.tool()
async def estimate_cloth_resources(
    ctx: Context,
    scene_name: str,
    collection_name: str | None = None,
    cloth_object_names: list[str] | None = None,
    object_limit: int = 25,
    object_offset: int = 0,
) -> dict:
    """Estimate bounded relative CPU, memory, cache, and contact pressure for scoped cloth objects.

    Indices are deterministic heuristics for comparing setups and choosing preview/final settings;
    they are not byte counts or bake-duration promises. Runtime PointCache facts are reported apart.
    """
    return await asyncio.to_thread(
        _call,
        "estimate_cloth_resources",
        {
            "scene_name": scene_name,
            "collection_name": collection_name,
            "cloth_object_names": cloth_object_names,
            "object_limit": object_limit,
            "object_offset": object_offset,
        },
    )


@mcp.tool()
async def validate_cloth_setup(
    ctx: Context,
    scene_name: str,
    collection_name: str | None = None,
    cloth_object_names: list[str] | None = None,
    max_findings: int = 200,
    collision_pair_limit: int = 64,
    evaluated_triangle_limit: int = 250_000,
) -> dict:
    """Run a bounded, non-mutating structural preflight over scoped cloth systems.

    Findings include severity, evidence, affected property/object/frame, and remediation. Continue
    with narrower scopes when ``truncated`` or the collision-pair limit is reported. Passing this
    check does not replace representative evaluated-frame review in Blender.
    """
    return await asyncio.to_thread(
        _call,
        "validate_cloth_setup",
        {
            "scene_name": scene_name,
            "collection_name": collection_name,
            "cloth_object_names": cloth_object_names,
            "max_findings": max_findings,
            "collision_pair_limit": collision_pair_limit,
            "evaluated_triangle_limit": evaluated_triangle_limit,
        },
    )
