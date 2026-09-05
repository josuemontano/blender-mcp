# MCP tool signatures intentionally expose more than five keyword arguments so
# agents receive precise JSON schemas instead of opaque catch-all dictionaries.
# ruff: file-ignore[docstring-missing-returns, too-many-arguments, too-many-positional-arguments]
"""Typed tools for cloth sewing, pressure, internal springs, rest shape, and field weights."""

import asyncio

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from ...app import mcp
from ._shared import _call, _dump, _StrictModel


class SewingPair(_StrictModel):
    """One explicit cross-panel sewing spring between two base-mesh vertices."""

    source_vertex: int = Field(ge=0)
    target_vertex: int = Field(ge=0)


class ClothPressurePatch(_StrictModel):
    """Allowlisted Blender 5.1 pressure properties."""

    use_pressure: bool | None = None
    uniform_pressure_force: float | None = None
    use_pressure_volume: bool | None = None
    target_volume: Annotated[float, Field(ge=0)] | None = None
    pressure_factor: Annotated[float, Field(ge=0)] | None = None
    fluid_density: Annotated[float, Field(gt=0)] | None = None
    vertex_group_pressure: Annotated[str, Field(min_length=1)] | None = None


class ClothInternalSpringsPatch(_StrictModel):
    """Allowlisted Blender 5.1 internal-spring properties."""

    use_internal_springs: bool | None = None
    internal_spring_max_length: Annotated[float, Field(ge=0)] | None = None
    internal_spring_max_diversion: Annotated[float, Field(ge=0)] | None = None
    internal_spring_normal_check: bool | None = None
    internal_tension_stiffness: Annotated[float, Field(ge=0)] | None = None
    internal_compression_stiffness: Annotated[float, Field(ge=0)] | None = None
    internal_tension_stiffness_max: Annotated[float, Field(ge=0)] | None = None
    internal_compression_stiffness_max: Annotated[float, Field(ge=0)] | None = None
    internal_friction: Annotated[float, Field(ge=0)] | None = None
    vertex_group_intern: Annotated[str, Field(min_length=1)] | None = None


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
    collection_name: Annotated[str, Field(min_length=1)] | None = None
    clear_collection: bool = False


@mcp.tool()
async def configure_cloth_sewing(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    seam_pairs: Annotated[list[SewingPair], Field(min_length=1)],
    sewing_force_max: Annotated[float, Field(gt=0)],
    create_missing_edges: bool = False,
    dry_run: bool = True,
    max_pair_distance: Annotated[float, Field(gt=0)] | None = None,
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
    max_estimated_springs: Annotated[int, Field(ge=1)] = 2_000_000,
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
    cache_frame_start: Annotated[int, Field(ge=0)],
    cache_frame_end: Annotated[int, Field(ge=0)],
) -> dict:
    """Assign one existing shape key as Cloth's rest shape over an explicit cache range.

    All shape keys and modifiers remain live and in their current order, and ``cache_frame_end`` must
    not precede ``cache_frame_start``. Dynamic mesh mode respects base-mesh deformation and can be
    expensive; topology-changing upstream modifiers are reported.
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
