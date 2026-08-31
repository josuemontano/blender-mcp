# ruff: file-ignore[docstring-missing-returns, too-many-arguments, too-many-positional-arguments, unused-function-argument]
"""Typed tools for evaluating and caching liquid simulations."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...app import mcp
from .inspection_and_setup import _call, _dump
from .mesh_and_materials import CacheMeshFormat

CacheAction = Literal[
    "STATUS",
    "CONFIGURE",
    "BAKE_DATA",
    "BAKE_GUIDES",
    "BAKE_MESH",
    "BAKE_PARTICLES",
    "BAKE_ALL",
    "PAUSE",
    "FREE_DATA",
    "FREE_GUIDES",
    "FREE_MESH",
    "FREE_PARTICLES",
    "FREE_ALL",
]
LiquidCacheType = Literal["REPLAY", "MODULAR", "FINAL", "ALL"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiquidCachePatch(_StrictModel):
    cache_directory: str | None = None
    cache_type: LiquidCacheType | None = None
    cache_data_format: Literal["UNI", "OPENVDB", "RAW"] | None = None
    cache_mesh_format: CacheMeshFormat | None = None
    cache_particle_format: Literal["UNI", "OPENVDB", "RAW"] | None = None
    cache_frame_start: int | None = None
    cache_frame_end: int | None = None
    cache_frame_offset: int | None = None
    cache_resumable: bool | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "LiquidCachePatch":
        if (
            self.cache_frame_start is not None
            and self.cache_frame_end is not None
            and self.cache_frame_start > self.cache_frame_end
        ):
            raise ValueError("cache_frame_start must be <= cache_frame_end")
        return self


@mcp.tool()
async def sample_liquid_simulation(
    ctx: Context,
    domain_object_name: str,
    modifier_name: str,
    frames: Annotated[list[int], Field(min_length=1, max_length=32)],
    timeout_seconds: Annotated[float, Field(gt=0.0, le=300.0)] = 30.0,
    boundary_tolerance_cells: Annotated[float, Field(ge=0.0, le=10.0)] = 1.0,
) -> dict:
    """Evaluate up to 32 cached/replay frames and return numerical mesh, particle, and bounds evidence."""
    return await asyncio.to_thread(
        _call,
        "sample_liquid_simulation",
        {
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "frames": frames,
            "timeout_seconds": timeout_seconds,
            "boundary_tolerance_cells": boundary_tolerance_cells,
        },
        [domain_object_name],
    )


@mcp.tool()
async def manage_liquid_cache(
    ctx: Context,
    domain_object_name: str,
    modifier_name: str,
    action: CacheAction = "STATUS",
    patch: LiquidCachePatch | None = None,
    confirm_bake: bool = False,
    confirm_free: bool = False,
    confirm_external_path: bool = False,
    confirm_external_overwrite: bool = False,
    max_bake_frames: Annotated[int, Field(ge=1, le=10_000)] = 250,
    max_existing_cache_bytes: Annotated[int, Field(ge=0)] = 10_000_000_000,
) -> dict:
    """Inspect, configure, bake, pause, or explicitly free exact Mantaflow cache stages."""
    return await asyncio.to_thread(
        _call,
        "manage_liquid_cache",
        {
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "action": action,
            "patch": _dump(patch),
            "confirm_bake": confirm_bake,
            "confirm_free": confirm_free,
            "confirm_external_path": confirm_external_path,
            "confirm_external_overwrite": confirm_external_overwrite,
            "max_bake_frames": max_bake_frames,
            "max_existing_cache_bytes": max_existing_cache_bytes,
        },
        [domain_object_name],
    )
