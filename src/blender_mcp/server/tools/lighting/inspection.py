"""Read-only MCP tools for lighting inventory, diagnosis, and compatibility validation."""

import asyncio

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from ...app import mcp
from ._shared import LightType, TargetEngine, call_blender


@mcp.tool()
async def list_lights(
    ctx: Context,
    scene_name: str,
    collection_name: str | None = None,
    light_type: LightType | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    offset: Annotated[int, Field(ge=0, le=9999)] = 0,
) -> dict:
    """Inventory scene lights without changing selection, mode, or the active object.

    Each record identifies the object and light datablock, world transform, energy/exposure,
    color or Kelvin temperature, shadows, type-specific shape controls, collections, target
    constraints, light group, and receiver/blocker collections. Continue with ``next_offset``
    while ``truncated`` is true. Filter by collection or light type when planning a focused edit.
    """
    return await asyncio.to_thread(
        call_blender,
        "list_lights",
        {
            "scene_name": scene_name,
            "collection_name": collection_name,
            "light_type": light_type,
            "limit": limit,
            "offset": offset,
        },
    )


@mcp.tool()
async def inspect_light(ctx: Context, scene_name: str, light_name: str) -> dict:
    """Inspect one light before editing it.

    The result separates local and world transforms, reports all shared and type-specific light
    settings, constraints, linking, animation, and a bounded summary of shader nodes and external
    image/IES dependencies. Compatibility notes distinguish shared, Cycles-only, and EEVEE behavior.
    """
    return await asyncio.to_thread(
        call_blender,
        "inspect_light",
        {"scene_name": scene_name, "light_name": light_name},
    )


@mcp.tool()
async def inspect_lighting_setup(
    ctx: Context,
    scene_name: str,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    offset: Annotated[int, Field(ge=0, le=9999)] = 0,
) -> dict:
    """Capture a reproducible, read-only scene-lighting snapshot.

    It includes the active engine, units, camera, color management, world graph, a paginated light
    inventory, bounded emissive/volume/probe inventories, hidden lights, light links/groups, and
    relevant Cycles and EEVEE quality settings. Use stable returned resource names in later tools.
    """
    return await asyncio.to_thread(
        call_blender,
        "inspect_lighting_setup",
        {"scene_name": scene_name, "limit": limit, "offset": offset},
    )


@mcp.tool()
async def validate_lighting_setup(
    ctx: Context,
    scene_name: str,
    target_engine: TargetEngine = "BOTH",
    subject_object_names: Annotated[list[str] | None, Field(max_length=100)] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 100,
    offset: Annotated[int, Field(ge=0, le=9999)] = 0,
) -> dict:
    """Audit lighting readiness without changing the scene.

    Findings contain severity, stable code, evidence, and remediation. The audit checks camera and
    engine availability, invalid or extreme power, coincident lights, disabled shadows, broken
    external files or linking, suspicious scale/exposure, and EEVEE probe risks. Supplying subjects
    also detects directional lights aimed away from them. ``BOTH`` explicitly reports cross-engine
    differences rather than claiming visual parity. Page findings with ``next_offset``.
    """
    return await asyncio.to_thread(
        call_blender,
        "validate_lighting_setup",
        {
            "scene_name": scene_name,
            "target_engine": target_engine,
            "subject_object_names": subject_object_names,
            "limit": limit,
            "offset": offset,
        },
    )
