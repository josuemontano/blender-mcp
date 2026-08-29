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
AttachmentType = Literal["HOOK", "ARMATURE", "MESH_DEFORM", "SURFACE_DEFORM"]
AnimationOwner = Literal[
    "CLOTH_SETTINGS",
    "EFFECTOR_WEIGHTS",
    "FIELD_SETTINGS",
    "COLLIDER_SETTINGS",
    "SHAPE_KEY",
    "MODIFIER",
    "OBJECT",
]
KeyframePolicy = Literal["INSERT_ONLY", "REPLACE_EXISTING"]
Interpolation = Literal["CONSTANT", "LINEAR", "BEZIER"]
CacheAction = Literal["INSPECT", "CONFIGURE", "BAKE", "BAKE_FROM_CACHE", "FREE"]
ClothComponentType = Literal[
    "CLOTH_MODIFIER",
    "COLLISION_MODIFIER",
    "ATTACHMENT_MODIFIER",
    "COLLISION_COLLECTION_MEMBERSHIP",
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


class SewingPair(_StrictModel):
    """One explicit cross-panel sewing spring between two base-mesh vertices."""

    source_vertex: int = Field(ge=0)
    target_vertex: int = Field(ge=0)


class ClothPressurePatch(_StrictModel):
    """Allowlisted Blender 5.1 pressure properties."""

    use_pressure: bool | None = None
    uniform_pressure_force: float | None = None
    use_pressure_volume: bool | None = None
    target_volume: float | None = None
    pressure_factor: float | None = None
    fluid_density: float | None = None
    vertex_group_pressure: str | None = None


class ClothInternalSpringsPatch(_StrictModel):
    """Allowlisted Blender 5.1 internal-spring properties."""

    use_internal_springs: bool | None = None
    internal_spring_max_length: float | None = None
    internal_spring_max_diversion: float | None = None
    internal_spring_normal_check: bool | None = None
    internal_tension_stiffness: float | None = None
    internal_compression_stiffness: float | None = None
    internal_tension_stiffness_max: float | None = None
    internal_compression_stiffness_max: float | None = None
    internal_friction: float | None = None
    vertex_group_intern: str | None = None


class ClothFieldWeightsPatch(_StrictModel):
    """Allowlisted Blender 5.1 EffectorWeights values and collection scope."""

    all: float | None = None
    gravity: float | None = None
    force: float | None = None
    vortex: float | None = None
    magnetic: float | None = None
    wind: float | None = None
    curve_guide: float | None = None
    texture: float | None = None
    harmonic: float | None = None
    charge: float | None = None
    lennardjones: float | None = None
    turbulence: float | None = None
    drag: float | None = None
    boid: float | None = None
    smokeflow: float | None = None
    apply_to_hair_growing: bool | None = None
    collection_name: str | None = None
    clear_collection: bool = False


class ClothAnimationKeyframe(_StrictModel):
    """One curated RNA property value at an exact frame."""

    owner: AnimationOwner
    property_name: str
    value: bool | int | float | tuple[float, float, float] | tuple[float, float, float, float]
    frame: float
    target_name: str | None = None
    array_index: int = Field(default=-1, ge=-1, le=3)
    interpolation: Interpolation = "BEZIER"


class PointCachePatch(_StrictModel):
    """Writable PointCache configuration fields."""

    frame_start: int | None = None
    frame_end: int | None = None
    frame_step: int | None = None
    name: str | None = None
    index: int | None = None
    use_disk_cache: bool | None = None
    use_external: bool | None = None
    use_library_path: bool | None = None
    filepath: str | None = None


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


@mcp.tool()
async def configure_cloth_sewing(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    seam_pairs: list[SewingPair],
    sewing_force_max: float,
    create_missing_edges: bool = False,
    dry_run: bool = True,
    max_pair_distance: float | None = None,
) -> dict:
    """Inspect or configure explicit loose-edge sewing springs on one cloth base mesh.

    Each pair creates or identifies one loose edge between two caller-selected boundary vertices.
    Dry-run is the default and never edits topology or settings. Enabling edge creation invalidates
    all previously queried topology indices; the tool never infers pairings or merges vertices.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_sewing",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "seam_pairs": [item.model_dump() for item in seam_pairs],
            "sewing_force_max": sewing_force_max,
            "create_missing_edges": create_missing_edges,
            "dry_run": dry_run,
            "max_pair_distance": max_pair_distance,
        },
        [] if dry_run else [object_name],
    )


@mcp.tool()
async def configure_cloth_pressure(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    patch: ClothPressurePatch,
) -> dict:
    """Configure pressure only after validating a closed, consistently oriented base mesh.

    The tool reports signed volume and orientation evidence but never seals holes or changes normals.
    Use animate_cloth_parameters for keyed pressure changes. Baked caches are rejected.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_pressure",
        {"object_name": object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [object_name],
    )


@mcp.tool()
async def configure_cloth_internal_springs(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    patch: ClothInternalSpringsPatch,
    max_estimated_springs: int = 2_000_000,
) -> dict:
    """Configure volumetric internal springs with a caller-visible conservative cost bound.

    Enabling springs requires a closed, consistently oriented mesh. Zero maximum length means no
    length limit and therefore uses the all-pairs upper bound. Dense unbounded requests are refused.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_internal_springs",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "patch": _dump(patch),
            "max_estimated_springs": max_estimated_springs,
        },
        [object_name],
    )


@mcp.tool()
async def configure_cloth_rest_shape(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    shape_key_name: str,
    use_dynamic_mesh: bool,
    cache_frame_start: int,
    cache_frame_end: int,
) -> dict:
    """Assign one existing shape key as Cloth's rest shape over an explicit cache range.

    All shape keys and modifiers remain live and in their current order. Dynamic mesh mode respects
    base-mesh deformation and can be expensive; topology-changing upstream modifiers are reported.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_rest_shape",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "shape_key_name": shape_key_name,
            "use_dynamic_mesh": use_dynamic_mesh,
            "cache_frame_start": cache_frame_start,
            "cache_frame_end": cache_frame_end,
        },
        [object_name],
    )


@mcp.tool()
async def configure_cloth_field_weights(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    patch: ClothFieldWeightsPatch,
) -> dict:
    """Patch Cloth EffectorWeights and an optional scene-linked effector collection.

    This does not create force fields. The cloth gravity vector remains a separate solver property;
    the result reports it beside the scalar EffectorWeights gravity multiplier.
    """
    return await asyncio.to_thread(
        _call,
        "configure_cloth_field_weights",
        {"object_name": object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [object_name],
    )


@mcp.tool()
async def animate_cloth_parameters(
    ctx: Context,
    object_name: str,
    keyframes: list[ClothAnimationKeyframe],
    cloth_modifier_name: str | None = None,
    policy: KeyframePolicy = "INSERT_ONLY",
) -> dict:
    """Insert exact keyframes on curated cloth-related RNA owners without touching unrelated curves.

    ``target_name`` selects a shape key or modifier for those owner kinds. INSERT_ONLY rejects an
    existing key at the same property/index/frame; REPLACE_EXISTING updates only that exact key.
    FIELD_SETTINGS currently permits force-field strength. Raw vertex-group membership is
    intentionally not animatable through this tool.
    """
    return await asyncio.to_thread(
        _call,
        "animate_cloth_parameters",
        {
            "object_name": object_name,
            "cloth_modifier_name": cloth_modifier_name,
            "keyframes": [item.model_dump() for item in keyframes],
            "policy": policy,
        },
        [object_name],
    )


@mcp.tool()
async def create_cloth_attachment(
    ctx: Context,
    cloth_object_name: str,
    cloth_modifier_name: str,
    pin_group_name: str,
    target_object_name: str,
    attachment_type: AttachmentType = "HOOK",
    attachment_modifier_name: str = "Cloth Attachment",
    bone_name: str | None = None,
    rest_frame: int = 1,
    existing_policy: ExistingPolicy = "ERROR",
    bind: bool = True,
) -> dict:
    """Create or reuse a typed attachment modifier immediately before Cloth.

    HOOK supports an optional armature bone and preserves the rest transform. ARMATURE,
    MESH_DEFORM, and SURFACE_DEFORM retain live targets; the deform variants bind only when
    ``bind`` is true. The pin group must already exist and is never modified.
    """
    return await asyncio.to_thread(
        _call,
        "create_cloth_attachment",
        {
            "cloth_object_name": cloth_object_name,
            "cloth_modifier_name": cloth_modifier_name,
            "pin_group_name": pin_group_name,
            "target_object_name": target_object_name,
            "attachment_type": attachment_type,
            "attachment_modifier_name": attachment_modifier_name,
            "bone_name": bone_name,
            "rest_frame": rest_frame,
            "existing_policy": existing_policy,
            "bind": bind,
        },
        [cloth_object_name, target_object_name],
    )


@mcp.tool()
async def create_character_cloth_setup(
    ctx: Context,
    garment_object_name: str,
    armature_object_name: str,
    body_collider_object_names: list[str],
    pin_group_name: str,
    collision_collection_name: str,
    cloth_modifier_name: str = "Cloth",
    armature_modifier_name: str = "Cloth Armature",
    collider_modifier_name: str = "Cloth Collision",
    subdivision_modifier_name: str = "Cloth Subdivision",
    solidify_modifier_name: str = "Cloth Solidify",
    existing_policy: ExistingPolicy = "ERROR",
    material: ClothMaterialPatch | None = None,
    solver: ClothSolverPatch | None = None,
    collisions: ClothCollisionPatch | None = None,
    collider_settings: ClothColliderPatch | None = None,
    add_subdivision: bool = False,
    subdivision_levels: int = 1,
    add_solidify: bool = False,
    solidify_thickness: float = 0.002,
    rest_frame: int = 1,
    cache_frame_start: int = 1,
    cache_frame_end: int = 250,
) -> dict:
    """Assemble a non-destructive garment, armature, collider, and finishing stack.

    All assets, the pin group, and collision collection must be explicitly named. No weights,
    collision proxies, or anatomy are inferred. Deformation is placed before Cloth and optional
    render-only Subdivision/Solidify modifiers after it; no modifier is applied.
    """
    return await asyncio.to_thread(
        _call,
        "create_character_cloth_setup",
        {
            "garment_object_name": garment_object_name,
            "armature_object_name": armature_object_name,
            "body_collider_object_names": body_collider_object_names,
            "pin_group_name": pin_group_name,
            "collision_collection_name": collision_collection_name,
            "cloth_modifier_name": cloth_modifier_name,
            "armature_modifier_name": armature_modifier_name,
            "collider_modifier_name": collider_modifier_name,
            "subdivision_modifier_name": subdivision_modifier_name,
            "solidify_modifier_name": solidify_modifier_name,
            "existing_policy": existing_policy,
            "material": _dump(material),
            "solver": _dump(solver),
            "collisions": _dump(collisions),
            "collider_settings": _dump(collider_settings),
            "add_subdivision": add_subdivision,
            "subdivision_levels": subdivision_levels,
            "add_solidify": add_solidify,
            "solidify_thickness": solidify_thickness,
            "rest_frame": rest_frame,
            "cache_frame_start": cache_frame_start,
            "cache_frame_end": cache_frame_end,
        },
        [garment_object_name, armature_object_name, *body_collider_object_names],
    )


@mcp.tool()
async def sample_cloth_simulation(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    frames: list[int],
    vertex_sample_limit: int = 10_000,
    collider_sample_limit: int = 16,
    timeout_seconds: float = 30.0,
) -> dict:
    """Evaluate bounded frames and measure the cloth without baking it.

    Sampling can populate or invalidate Blender's in-memory point cache and is therefore mutating.
    The original frame is restored in ``finally``. Returned penetration evidence is heuristic and
    representative-frame review remains necessary.
    """
    return await asyncio.to_thread(
        _call,
        "sample_cloth_simulation",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "frames": frames,
            "vertex_sample_limit": vertex_sample_limit,
            "collider_sample_limit": collider_sample_limit,
            "timeout_seconds": timeout_seconds,
        },
        [object_name],
    )


@mcp.tool()
async def manage_cloth_cache(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    action: CacheAction = "INSPECT",
    patch: PointCachePatch | None = None,
    confirm_bake: bool = False,
    confirm_free_bake: bool = False,
    confirm_external_overwrite: bool = False,
    max_bake_frames: int = 250,
) -> dict:
    """Inspect, configure, bake, mark baked-from-cache, or free one exact Cloth point cache.

    BAKE is synchronous and requires confirmation; requests exceeding ``max_bake_frames`` are
    refused so this command never launches an unbounded job. FREE requires separate confirmation.
    Baking into or freeing an external cache directory containing files also requires explicit
    overwrite/deletion confirmation.
    """
    return await asyncio.to_thread(
        _call,
        "manage_cloth_cache",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "action": action,
            "patch": _dump(patch),
            "confirm_bake": confirm_bake,
            "confirm_free_bake": confirm_free_bake,
            "confirm_external_overwrite": confirm_external_overwrite,
            "max_bake_frames": max_bake_frames,
        },
        [] if action == "INSPECT" else [object_name],
    )


@mcp.tool()
async def remove_cloth_components(
    ctx: Context,
    object_name: str,
    component_type: ClothComponentType,
    modifier_name: str | None = None,
    collection_name: str | None = None,
    confirm_baked_removal: bool = False,
    confirm_affected_bakes: bool = False,
) -> dict:
    """Remove one exact cloth-related component while retaining unrelated data and cache files.

    Modifier targets require their exact name; collection membership requires its exact collection.
    Vertex groups, meshes, materials, controls, and external cache files are never deleted. Baked
    cloth or affected baked dependencies require the corresponding explicit confirmation flag.
    """
    return await asyncio.to_thread(
        _call,
        "remove_cloth_components",
        {
            "object_name": object_name,
            "component_type": component_type,
            "modifier_name": modifier_name,
            "collection_name": collection_name,
            "confirm_baked_removal": confirm_baked_removal,
            "confirm_affected_bakes": confirm_affected_bakes,
        },
        [object_name],
    )
