"""Authored-animation and rigid-body handoff tools."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import Field

from .inspection_and_setup import Vector3, _call, mcp


@mcp.tool()
async def animate_rigid_body_release(
    ctx: Context,
    scene_name: str,
    object_name: str,
    transition: Literal["RELEASE", "CAPTURE"],
    frame: Annotated[int, Field(ge=-1_000_000, le=1_000_000)],
    pre_roll_frames: Annotated[int, Field(ge=1, le=120)] = 1,
    linear_velocity: Vector3 | None = None,
    angular_velocity: Vector3 | None = None,
    action_name: str | None = None,
    overwrite_existing_action: bool = False,
    confirm_delete_baked_cache: bool = False,
) -> dict:
    """Key a deterministic handoff between authored transforms and rigid-body simulation."""
    return await asyncio.to_thread(
        _call,
        "animate_rigid_body_release",
        {
            "scene_name": scene_name,
            "object_name": object_name,
            "transition": transition,
            "frame": frame,
            "pre_roll_frames": pre_roll_frames,
            "linear_velocity": linear_velocity,
            "angular_velocity": angular_velocity,
            "action_name": action_name,
            "overwrite_existing_action": overwrite_existing_action,
            "confirm_delete_baked_cache": confirm_delete_baked_cache,
        },
        [object_name],
    )
