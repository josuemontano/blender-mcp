# MCP tool signatures intentionally expose explicit keyword arguments so agents
# receive a useful schema instead of an unrestricted FluidDomainSettings bag.
# Ruff's argument-count and unused-context rules conflict with FastMCP's typed
# public signatures; return sections are carried by the generated tool schema.
# ruff: file-ignore[docstring-missing-returns, multi-line-summary-second-line, too-many-arguments, too-many-positional-arguments, too-many-statements-in-try-clause, undocumented-public-method, unused-function-argument]
"""Typed tools for liquid domain inspection, setup, flows, effectors, and validation."""

import asyncio
import logging
import sys

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...app import mcp
from ...connection import get_blender_connection
from ..envelope import ok

logger = logging.getLogger("BlenderMCPServer")

CacheType = Literal["REPLAY", "MODULAR", "ALL"]
ExistingPolicy = Literal["ERROR", "REUSE"]
FlowBehavior = Literal["GEOMETRY", "INFLOW", "OUTFLOW"]
EffectorType = Literal["COLLISION", "GUIDE"]
GuideMode = Literal["MAXIMUM", "MINIMUM", "OVERRIDE", "AVERAGED"]
SimulationMethod = Literal["FLIP", "APIC"]
BoundaryFace = Literal["FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiquidSolverPatch(_StrictModel):
    """Allowlisted Blender 5.1 liquid solver properties."""

    resolution_max: int | None = Field(default=None, ge=6, le=10_000)
    time_scale: float | None = Field(default=None, gt=0.0)
    timesteps_min: int | None = Field(default=None, ge=1)
    timesteps_max: int | None = Field(default=None, ge=1)
    use_adaptive_timesteps: bool | None = None
    cfl_condition: float | None = Field(default=None, gt=0.0)
    simulation_method: SimulationMethod | None = None
    flip_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    particle_randomness: float | None = Field(default=None, ge=0.0)
    particle_number: int | None = Field(default=None, ge=1)
    particle_min: int | None = Field(default=None, ge=0)
    particle_max: int | None = Field(default=None, ge=1)
    particle_radius: float | None = Field(default=None, gt=0.0)
    particle_band_width: float | None = Field(default=None, gt=0.0)
    use_fractions: bool | None = None
    fractions_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    fractions_distance: float | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "LiquidSolverPatch":
        if (
            self.timesteps_min is not None
            and self.timesteps_max is not None
            and self.timesteps_min > self.timesteps_max
        ):
            raise ValueError("timesteps_min must be <= timesteps_max")
        if self.particle_min is not None and self.particle_max is not None and self.particle_min > self.particle_max:
            raise ValueError("particle_min must be <= particle_max")
        return self


class LiquidFlowPatch(_StrictModel):
    """Allowlisted liquid-applicable FluidFlowSettings properties."""

    flow_behavior: FlowBehavior | None = None
    use_inflow: bool | None = None
    use_plane_init: bool | None = None
    surface_distance: float | None = Field(default=None, ge=0.0)
    subframes: int | None = Field(default=None, ge=0)
    use_initial_velocity: bool | None = None
    velocity_coord: tuple[float, float, float] | None = None
    velocity_factor: float | None = None
    velocity_normal: float | None = None
    velocity_random: float | None = Field(default=None, ge=0.0)
    use_particle_size: bool | None = None
    particle_size: float | None = Field(default=None, gt=0.0)
    density_vertex_group: str | None = None


class LiquidEffectorPatch(_StrictModel):
    """Allowlisted FluidEffectorSettings properties."""

    use_effector: bool | None = None
    effector_type: EffectorType | None = None
    use_plane_init: bool | None = None
    surface_distance: float | None = Field(default=None, ge=0.0)
    subframes: int | None = Field(default=None, ge=0)
    guide_mode: GuideMode | None = None
    velocity_factor: float | None = None


class LiquidBoundaryPatch(_StrictModel):
    """Collision state for domain-local faces."""

    front: bool | None = None
    back: bool | None = None
    left: bool | None = None
    right: bool | None = None
    top: bool | None = None
    bottom: bool | None = None


def _dump(model: BaseModel | None) -> dict | None:
    return model.model_dump(exclude_none=True, exclude_unset=True) if model is not None else None


def _connection_call(command: str, params: dict, changed_objects: list[str] | None = None) -> dict:
    try:
        result = get_blender_connection().send_command(command, params)
        changed = result.get("changed_objects", changed_objects or []) if isinstance(result, dict) else changed_objects
        resources = result.get("changed_resources", []) if isinstance(result, dict) else []
        warnings = result.get("warnings", []) if isinstance(result, dict) else []
        if isinstance(result, dict):
            result = {
                key: value
                for key, value in result.items()
                if key not in {"changed_objects", "changed_resources", "warnings"}
            }
        envelope = ok(result, changed_objects=changed or [], changed_resources=resources)
        envelope["warnings"] = warnings
        return envelope
    except Exception as exc:
        logger.error("Error running %s: %s", command, exc)
        raise ToolError(f"Error running {command}: {exc}") from exc


def _call(command: str, params: dict, changed_objects: list[str] | None = None) -> dict:
    """Dispatch through the package hook so tests and embedders can replace the transport."""
    package = sys.modules.get(__package__) if __package__ is not None else None
    override = getattr(package, "_call", None) if package is not None else None
    if override is not None and override is not _call:
        return override(command, params, changed_objects)
    return _connection_call(command, params, changed_objects)


@mcp.tool()
async def get_liquid_simulation_info(
    ctx: Context,
    scene_name: Annotated[str | None, Field(min_length=1)] = None,
    domain_object_name: str | None = None,
    domain_limit: Annotated[int, Field(ge=1, le=100)] = 25,
    domain_offset: Annotated[int, Field(ge=0)] = 0,
    dependency_limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    dependency_offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """Inspect liquid domains, scoped dependencies, cache stages, and outputs without evaluating frames.

    Supply a scene, one domain, or both. Domain and dependency pages have independent offsets.
    Coordinates and bounds are labelled; this call never initializes or advances a simulation.
    """
    return await asyncio.to_thread(
        _call,
        "get_liquid_simulation_info",
        {
            "scene_name": scene_name,
            "domain_object_name": domain_object_name,
            "domain_limit": domain_limit,
            "domain_offset": domain_offset,
            "dependency_limit": dependency_limit,
            "dependency_offset": dependency_offset,
        },
    )


@mcp.tool()
async def get_fluid_object_info(ctx: Context, object_name: str) -> dict:
    """Inspect one domain, liquid flow, or fluid effector, including transforms and evaluated bounds."""
    return await asyncio.to_thread(_call, "get_fluid_object_info", {"object_name": object_name})


@mcp.tool()
async def create_liquid_domain(
    ctx: Context,
    scene_name: Annotated[str, Field(min_length=1)],
    cache_directory: Annotated[str, Field(min_length=1)],
    object_name: str | None = None,
    new_object_name: str = "Liquid Domain",
    collection_name: str | None = None,
    dimensions: tuple[float, float, float] = (4.0, 4.0, 4.0),
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    modifier_name: str = "Liquid Domain",
    flow_collection_name: str | None = None,
    effector_collection_name: str | None = None,
    cache_type: CacheType = "REPLAY",
    cache_frame_start: int = 1,
    cache_frame_end: int = 250,
    resolution_max: Annotated[int, Field(ge=6, le=10_000)] = 64,
    simulation_method: SimulationMethod = "FLIP",
    time_scale: Annotated[float, Field(gt=0.0)] = 1.0,
    use_adaptive_timesteps: bool = True,
    timesteps_min: Annotated[int, Field(ge=1)] = 1,
    timesteps_max: Annotated[int, Field(ge=1)] = 4,
    cfl_condition: Annotated[float, Field(gt=0.0)] = 4.0,
) -> dict:
    """Create a unit-scale box domain or add a live liquid domain to an explicit mesh.

    The cache path is configured but no directory, cache, or bake is created. New boxes bake their
    requested dimensions into mesh coordinates; dimensions must be three positive, finite values.
    Named flow/effector collections are created or reused. cache_frame_start must be <=
    cache_frame_end and timesteps_min must be <= timesteps_max. When object_name is given, that mesh
    must already have vertices/faces, non-zero finite scale, and no existing fluid domain modifier.
    """
    return await asyncio.to_thread(
        _call,
        "create_liquid_domain",
        {
            "scene_name": scene_name,
            "cache_directory": cache_directory,
            "object_name": object_name,
            "new_object_name": new_object_name,
            "collection_name": collection_name,
            "dimensions": dimensions,
            "location": location,
            "modifier_name": modifier_name,
            "flow_collection_name": flow_collection_name,
            "effector_collection_name": effector_collection_name,
            "cache_type": cache_type,
            "cache_frame_start": cache_frame_start,
            "cache_frame_end": cache_frame_end,
            "resolution_max": resolution_max,
            "simulation_method": simulation_method,
            "time_scale": time_scale,
            "use_adaptive_timesteps": use_adaptive_timesteps,
            "timesteps_min": timesteps_min,
            "timesteps_max": timesteps_max,
            "cfl_condition": cfl_condition,
        },
        [object_name] if object_name else None,
    )


@mcp.tool()
async def fit_liquid_domain(
    ctx: Context,
    scene_name: Annotated[str, Field(min_length=1)],
    source_object_names: Annotated[list[str], Field(min_length=1)],
    collider_object_names: list[str] | None = None,
    domain_object_name: str | None = None,
    new_domain_name: str = "Liquid Domain",
    cache_directory: Annotated[str | None, Field(min_length=1)] = None,
    collection_name: str | None = None,
    modifier_name: str = "Liquid Domain",
    padding: tuple[float, float, float] = (0.25, 0.25, 0.25),
    expected_travel: tuple[float, float, float] = (0.0, 0.0, 0.0),
    splash_height: Annotated[float, Field(ge=0.0)] = 0.0,
    sample_frame_start: int | None = None,
    sample_frame_end: int | None = None,
    sample_frame_step: Annotated[int, Field(ge=1)] = 1,
    open_boundaries: list[BoundaryFace] | None = None,
) -> dict:
    """Fit an unbaked domain to bounded sampled world-space source/collider motion.

    With no domain name, a new unit-scale box is created and ``cache_directory`` is required. Frames
    are sampled from sample_frame_start through sample_frame_end (sample_frame_end must be >=
    sample_frame_start; at most 32 frames total, narrow the range or raise sample_frame_step if that
    is exceeded), and the original current frame is always restored afterward. padding must be three
    non-negative values; existing object transforms are kept. Refitting an existing domain requires
    its mesh datablock to be single-user (not shared with another object).
    """
    return await asyncio.to_thread(
        _call,
        "fit_liquid_domain",
        {
            "scene_name": scene_name,
            "source_object_names": source_object_names,
            "collider_object_names": collider_object_names or [],
            "domain_object_name": domain_object_name,
            "new_domain_name": new_domain_name,
            "cache_directory": cache_directory,
            "collection_name": collection_name,
            "modifier_name": modifier_name,
            "padding": padding,
            "expected_travel": expected_travel,
            "splash_height": splash_height,
            "sample_frame_start": sample_frame_start,
            "sample_frame_end": sample_frame_end,
            "sample_frame_step": sample_frame_step,
            "open_boundaries": open_boundaries or [],
        },
        [name for name in [domain_object_name, *source_object_names, *(collider_object_names or [])] if name],
    )


@mcp.tool()
async def configure_liquid_solver(
    ctx: Context, domain_object_name: str, modifier_name: str, patch: LiquidSolverPatch
) -> dict:
    """Patch only supplied core liquid solver fields on one unbaked domain.

    patch must set at least one field. If both are given, timesteps_min must be <= timesteps_max and
    particle_min must be <= particle_max (enforced by LiquidSolverPatch).
    """
    return await asyncio.to_thread(
        _call,
        "configure_liquid_solver",
        {"domain_object_name": domain_object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [domain_object_name],
    )


@mcp.tool()
async def add_liquid_flow(
    ctx: Context,
    object_name: str,
    domain_object_name: str,
    modifier_name: str = "Liquid Flow",
    existing_policy: ExistingPolicy = "ERROR",
    behavior: FlowBehavior = "GEOMETRY",
    settings: LiquidFlowPatch | None = None,
) -> dict:
    """Add or explicitly reuse a liquid mesh flow and register it with one unbaked domain.

    Blender 5.1 exposes mesh emission for this surface; unsupported particle sources are rejected
    instead of assigning the unstable dynamic ``flow_source`` enum. object_name and domain_object_name
    must differ - a domain cannot also be its own flow. existing_policy="REUSE" requires object_name
    to already carry a LIQUID flow modifier; existing_policy="ERROR" fails if modifier_name is taken.
    """
    return await asyncio.to_thread(
        _call,
        "add_liquid_flow",
        {
            "object_name": object_name,
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "existing_policy": existing_policy,
            "behavior": behavior,
            "settings": _dump(settings),
        },
        [object_name, domain_object_name],
    )


@mcp.tool()
async def configure_liquid_flow(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    domain_object_name: str,
    patch: LiquidFlowPatch,
) -> dict:
    """Patch emission, subframes, and velocity on an existing domain-associated liquid flow.

    patch must set at least one field. patch.use_inflow only has an effect when the flow's behavior
    is INFLOW. patch.density_vertex_group, if set, must name an existing vertex group on object_name.
    """
    return await asyncio.to_thread(
        _call,
        "configure_liquid_flow",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "domain_object_name": domain_object_name,
            "patch": _dump(patch),
        },
        [object_name, domain_object_name],
    )


@mcp.tool()
async def add_liquid_effector(
    ctx: Context,
    object_name: str,
    domain_object_name: str,
    modifier_name: str = "Liquid Effector",
    existing_policy: ExistingPolicy = "ERROR",
    effector_type: EffectorType = "COLLISION",
    settings: LiquidEffectorPatch | None = None,
) -> dict:
    """Add or reuse a collision/guide effector and register it without changing existing links or animation.

    object_name and domain_object_name must differ - a domain cannot also be its own effector.
    existing_policy="REUSE" requires object_name to already carry a LIQUID effector modifier;
    existing_policy="ERROR" fails if modifier_name is taken.
    """
    return await asyncio.to_thread(
        _call,
        "add_liquid_effector",
        {
            "object_name": object_name,
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "existing_policy": existing_policy,
            "effector_type": effector_type,
            "settings": _dump(settings),
        },
        [object_name, domain_object_name],
    )


@mcp.tool()
async def configure_liquid_effector(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    domain_object_name: str,
    patch: LiquidEffectorPatch,
) -> dict:
    """Patch an existing domain-associated liquid collision or guide effector.

    patch must set at least one field.
    """
    return await asyncio.to_thread(
        _call,
        "configure_liquid_effector",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "domain_object_name": domain_object_name,
            "patch": _dump(patch),
        },
        [object_name, domain_object_name],
    )


@mcp.tool()
async def configure_liquid_scope_and_boundaries(
    ctx: Context,
    domain_object_name: str,
    modifier_name: str,
    flow_collection_name: str | None = None,
    effector_collection_name: str | None = None,
    force_collection_name: str | None = None,
    clear_flow_collection: bool = False,
    clear_effector_collection: bool = False,
    clear_force_collection: bool = False,
    create_missing_collections: bool = False,
    boundaries: LiquidBoundaryPatch | None = None,
) -> dict:
    """Set domain collection scopes and domain-local collision faces on an unbaked domain.

    ``True`` means the local face collides; ``False`` opens it. The returned world matrix makes the
    local FRONT/BACK/LEFT/RIGHT/TOP/BOTTOM mapping explicit. At least one collection name, clear_*
    flag, or boundaries field must be given - calling this with every parameter left at its default
    is rejected. For each of flow/effector/force, setting the matching *_collection_name and clear_*
    flag together in the same call is also rejected.
    """
    return await asyncio.to_thread(
        _call,
        "configure_liquid_scope_and_boundaries",
        {
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "flow_collection_name": flow_collection_name,
            "effector_collection_name": effector_collection_name,
            "force_collection_name": force_collection_name,
            "clear_flow_collection": clear_flow_collection,
            "clear_effector_collection": clear_effector_collection,
            "clear_force_collection": clear_force_collection,
            "create_missing_collections": create_missing_collections,
            "boundaries": _dump(boundaries),
        },
        [domain_object_name],
    )


@mcp.tool()
async def estimate_liquid_resources(ctx: Context, domain_object_name: str, modifier_name: str) -> dict:
    """Estimate grid dimensions and conservative relative cache cost without changing the domain."""
    return await asyncio.to_thread(
        _call,
        "estimate_liquid_resources",
        {"domain_object_name": domain_object_name, "modifier_name": modifier_name},
    )


@mcp.tool()
async def validate_liquid_setup(
    ctx: Context,
    scene_name: Annotated[str, Field(min_length=1)],
    domain_object_names: list[str] | None = None,
    max_findings: Annotated[int, Field(ge=1, le=1000)] = 200,
) -> dict:
    """Run a bounded non-mutating preflight over liquid domains, dependencies, cache, and output readiness.

    Omitting domain_object_names (or passing an empty list) validates every liquid domain in the
    scene. The result's "truncated" flag indicates more findings existed than max_findings allowed
    through.
    """
    return await asyncio.to_thread(
        _call,
        "validate_liquid_setup",
        {
            "scene_name": scene_name,
            "domain_object_names": domain_object_names,
            "max_findings": max_findings,
        },
    )
