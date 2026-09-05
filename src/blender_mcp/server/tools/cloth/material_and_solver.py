# ruff: file-ignore[docstring-missing-returns]
"""Typed tools for patching cloth material and solver-only properties."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import Field

from ...app import mcp
from ._shared import _call, _dump, _StrictModel

MaterialPreset = Literal["COTTON", "SILK", "DENIM", "LEATHER", "RUBBER"]


class ClothMaterialPatch(_StrictModel):
    """Allowlisted Blender 5.1 cloth material properties."""

    mass: Annotated[float, Field(gt=0)] | None = None
    air_damping: Annotated[float, Field(ge=0)] | None = None
    bending_model: Literal["ANGULAR", "LINEAR"] | None = None
    tension_stiffness: Annotated[float, Field(ge=0)] | None = None
    tension_stiffness_max: Annotated[float, Field(ge=0)] | None = None
    compression_stiffness: Annotated[float, Field(ge=0)] | None = None
    compression_stiffness_max: Annotated[float, Field(ge=0)] | None = None
    shear_stiffness: Annotated[float, Field(ge=0)] | None = None
    shear_stiffness_max: Annotated[float, Field(ge=0)] | None = None
    bending_stiffness: Annotated[float, Field(ge=0)] | None = None
    bending_stiffness_max: Annotated[float, Field(ge=0)] | None = None
    tension_damping: Annotated[float, Field(ge=0)] | None = None
    compression_damping: Annotated[float, Field(ge=0)] | None = None
    shear_damping: Annotated[float, Field(ge=0)] | None = None
    bending_damping: Annotated[float, Field(ge=0)] | None = None


class ClothSolverPatch(_StrictModel):
    """Allowlisted solver controls, deliberately excluding material and collision settings."""

    quality: Annotated[int, Field(ge=1)] | None = None
    time_scale: Annotated[float, Field(ge=0)] | None = None
    gravity: tuple[float, float, float] | None = None
    voxel_cell_size: Annotated[float, Field(gt=0)] | None = None


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
