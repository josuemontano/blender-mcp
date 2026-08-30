"""Force-field authoring for rigid-body worlds."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field

from .inspection_and_setup import RigidBodyEffectorWeightsPatch, Vector3, _call, mcp


class RigidBodyForceField(BaseModel):
    """A bounded force-field object specification."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    object_name: str = Field(min_length=1)
    field_type: Literal["FORCE", "WIND", "VORTEX", "TURBULENCE", "DRAG", "HARMONIC"]
    create_if_missing: bool = False
    location: Vector3 = (0.0, 0.0, 0.0)
    rotation_euler: Vector3 = (0.0, 0.0, 0.0)
    strength: float | None = None
    flow: float | None = None
    noise: float | None = Field(default=None, ge=0.0, le=10.0)
    seed: int | None = Field(default=None, ge=1, le=128)
    shape: Literal["POINT", "PLANE", "SURFACE", "EVERY_POINT"] | None = None
    falloff_type: Literal["SPHERE", "TUBE", "CONE"] | None = None
    falloff_power: float | None = Field(default=None, ge=0.0, le=10.0)
    distance_min: float | None = Field(default=None, ge=0.0)
    distance_max: float | None = Field(default=None, ge=0.0)
    apply_to_location: bool | None = None
    apply_to_rotation: bool | None = None


@mcp.tool()
async def configure_rigid_body_force_fields(
    ctx: Context,
    scene_name: str,
    force_collection_name: str,
    fields: Annotated[list[RigidBodyForceField], Field(max_length=64)],
    create_collection: bool = False,
    weights: RigidBodyEffectorWeightsPatch | None = None,
    confirm_delete_baked_cache: bool = False,
) -> dict:
    """Create or patch force fields and the rigid-body world's effector weights."""
    return await asyncio.to_thread(
        _call,
        "configure_rigid_body_force_fields",
        {
            "scene_name": scene_name,
            "force_collection_name": force_collection_name,
            "fields": [field.model_dump(exclude_none=True) for field in fields],
            "create_collection": create_collection,
            "weights": weights.model_dump(exclude_none=True, exclude_unset=True) if weights else {},
            "confirm_delete_baked_cache": confirm_delete_baked_cache,
        },
        [field.object_name for field in fields],
    )
