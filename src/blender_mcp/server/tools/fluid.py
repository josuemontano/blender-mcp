# ruff: file-ignore[docstring-missing-returns, too-many-arguments, too-many-positional-arguments, unused-function-argument]
"""Canonical Mantaflow tools shared by liquid and gas workflows."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import Field, model_validator

from ..app import mcp
from .liquid._shared import _call, _dump
from .liquid._shared import _FiniteStrictModel as _StrictModel
from .liquid.inspection_and_setup import (
    CacheType,
    EffectorType,
    ExistingPolicy,
    FlowBehavior,
    LiquidEffectorPatch,
)
from .liquid.simulation import LiquidCachePatch

FluidDomainType = Literal["LIQUID", "GAS"]
FluidBakeStage = Literal["DATA", "GUIDES", "MESH", "PARTICLES", "NOISE", "ALL"]
FluidCacheAction = Literal[
    "STATUS",
    "CONFIGURE",
    "BAKE_DATA",
    "BAKE_GUIDES",
    "BAKE_MESH",
    "BAKE_PARTICLES",
    "BAKE_NOISE",
    "BAKE_ALL",
    "START_BAKE",
    "RESUME",
    "CANCEL",
    "PAUSE",
    "FREE_DATA",
    "FREE_GUIDES",
    "FREE_MESH",
    "FREE_PARTICLES",
    "FREE_NOISE",
    "FREE_ALL",
]


class FluidSolverPatch(_StrictModel):
    """Common and domain-specific Mantaflow solver settings."""

    resolution_max: Annotated[int | None, Field(ge=6, le=10_000)] = None
    time_scale: Annotated[float | None, Field(gt=0)] = None
    timesteps_min: Annotated[int | None, Field(ge=1)] = None
    timesteps_max: Annotated[int | None, Field(ge=1)] = None
    use_adaptive_timesteps: bool | None = None
    cfl_condition: Annotated[float | None, Field(gt=0)] = None
    simulation_method: Literal["FLIP", "APIC"] | None = None
    flip_ratio: Annotated[float | None, Field(ge=0, le=1)] = None
    vorticity: Annotated[float | None, Field(ge=0)] = None
    burning_rate: Annotated[float | None, Field(ge=0)] = None
    flame_smoke: Annotated[float | None, Field(ge=0)] = None
    flame_vorticity: Annotated[float | None, Field(ge=0)] = None
    use_noise: bool | None = None
    noise_scale: Annotated[int | None, Field(ge=1)] = None

    @model_validator(mode="after")
    def validate_patch(self) -> "FluidSolverPatch":
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        if (
            self.timesteps_min is not None
            and self.timesteps_max is not None
            and self.timesteps_min > self.timesteps_max
        ):
            raise ValueError("timesteps_min must be <= timesteps_max")
        return self


class FluidFlowPatch(_StrictModel):
    """Common liquid or gas flow settings."""

    flow_behavior: FlowBehavior | None = None
    use_inflow: bool | None = None
    use_plane_init: bool | None = None
    surface_distance: Annotated[float | None, Field(ge=0)] = None
    subframes: Annotated[int | None, Field(ge=0)] = None
    use_initial_velocity: bool | None = None
    velocity_coord: tuple[float, float, float] | None = None
    density: Annotated[float | None, Field(ge=0)] = None
    fuel_amount: Annotated[float | None, Field(ge=0)] = None
    smoke_color: tuple[float, float, float] | None = None
    temperature: float | None = None


@mcp.tool()
async def inspect_fluid_simulation(
    ctx: Context,
    domain_type: FluidDomainType,
    scene_name: str | None = None,
    domain_object_name: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """Inspect bounded liquid or gas domain state through the canonical fluid surface."""
    return await asyncio.to_thread(
        _call,
        "inspect_fluid_simulation",
        {
            "domain_type": domain_type,
            "scene_name": scene_name,
            "domain_object_name": domain_object_name,
            "limit": limit,
            "offset": offset,
        },
    )


@mcp.tool()
async def create_fluid_domain(
    ctx: Context,
    domain_type: FluidDomainType,
    scene_name: Annotated[str, Field(min_length=1)],
    cache_directory: Annotated[str, Field(min_length=1)],
    object_name: str | None = None,
    new_object_name: str = "Fluid Domain",
    collection_name: str | None = None,
    dimensions: tuple[float, float, float] = (4.0, 4.0, 4.0),
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    modifier_name: str = "Fluid Domain",
    flow_collection_name: str | None = None,
    effector_collection_name: str | None = None,
    cache_type: CacheType = "REPLAY",
    cache_frame_start: int = 1,
    cache_frame_end: int = 250,
    resolution_max: Annotated[int, Field(ge=6, le=10_000)] = 64,
) -> dict:
    """Create a live LIQUID or GAS Mantaflow domain with isolated collections and cache path."""
    if cache_frame_end < cache_frame_start:
        raise ValueError("cache_frame_end must be >= cache_frame_start")
    return await asyncio.to_thread(
        _call,
        "create_fluid_domain",
        {key: value for key, value in locals().items() if key != "ctx"},
        [object_name] if object_name else None,
    )


@mcp.tool()
async def configure_fluid_solver(
    ctx: Context,
    domain_type: FluidDomainType,
    domain_object_name: str,
    modifier_name: str,
    patch: FluidSolverPatch,
) -> dict:
    """Patch validated common or domain-specific solver settings without touching omitted fields."""
    return await asyncio.to_thread(
        _call,
        "configure_fluid_solver",
        {
            "domain_type": domain_type,
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "patch": _dump(patch),
        },
        [domain_object_name],
    )


@mcp.tool()
async def add_fluid_flow(
    ctx: Context,
    domain_type: FluidDomainType,
    object_name: str,
    domain_object_name: str,
    modifier_name: str = "Fluid Flow",
    existing_policy: ExistingPolicy = "ERROR",
    behavior: FlowBehavior = "GEOMETRY",
    gas_flow_type: Literal["SMOKE", "FIRE", "BOTH"] = "SMOKE",
    settings: FluidFlowPatch | None = None,
) -> dict:
    """Add a liquid or gas mesh flow and register it with one explicit domain."""
    return await asyncio.to_thread(
        _call,
        "add_fluid_flow",
        {
            "domain_type": domain_type,
            "object_name": object_name,
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "existing_policy": existing_policy,
            "behavior": behavior,
            "gas_flow_type": gas_flow_type,
            "settings": _dump(settings),
        },
        [object_name, domain_object_name],
    )


@mcp.tool()
async def add_fluid_effector(
    ctx: Context,
    domain_type: FluidDomainType,
    object_name: str,
    domain_object_name: str,
    modifier_name: str = "Fluid Effector",
    existing_policy: ExistingPolicy = "ERROR",
    effector_type: EffectorType = "COLLISION",
    settings: LiquidEffectorPatch | None = None,
) -> dict:
    """Add a shared collision/guide effector to a LIQUID or GAS domain."""
    return await asyncio.to_thread(
        _call,
        "add_fluid_effector",
        {
            "domain_type": domain_type,
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
async def manage_fluid_cache(
    ctx: Context,
    domain_type: FluidDomainType,
    domain_object_name: str,
    modifier_name: str,
    action: FluidCacheAction = "STATUS",
    patch: LiquidCachePatch | None = None,
    stage: FluidBakeStage | None = None,
    confirm_bake: bool = False,
    confirm_free: bool = False,
    confirm_external_path: bool = False,
    confirm_external_overwrite: bool = False,
    max_bake_frames: Annotated[int, Field(ge=1, le=10_000)] = 250,
) -> dict:
    """Manage a normalized Mantaflow cache lifecycle for LIQUID or GAS domains."""
    return await asyncio.to_thread(
        _call,
        "manage_fluid_cache",
        {
            "domain_type": domain_type,
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "action": action,
            "patch": _dump(patch),
            "stage": stage,
            "confirm_bake": confirm_bake,
            "confirm_free": confirm_free,
            "confirm_external_path": confirm_external_path,
            "confirm_external_overwrite": confirm_external_overwrite,
            "max_bake_frames": max_bake_frames,
        },
        [domain_object_name],
    )
