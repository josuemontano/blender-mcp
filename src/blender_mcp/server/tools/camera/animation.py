"""Typed tools for camera-rig keyframing and time-based shot effects."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field, model_validator

from ...app import mcp
from ._shared import _call, _StrictModel, _tool_params

AnimationOwner = Literal["OBJECT", "CAMERA_DATA", "CONSTRAINT", "DOF"]
KeyPolicy = Literal["REPLACE", "INSERT_ONLY"]
Interpolation = Literal["CONSTANT", "LINEAR", "BEZIER"]
HandleType = Literal["FREE", "ALIGNED", "VECTOR", "AUTO", "AUTO_CLAMPED"]
FocusPullMode = Literal["DISTANCE", "FOCUS_CONTROL"]
FramingAxis = Literal["HORIZONTAL", "VERTICAL"]


class CameraKeyframe(_StrictModel):
    """One allowlisted camera-rig channel value at one frame."""

    object_name: str = Field(min_length=1)
    owner: AnimationOwner = "OBJECT"
    constraint_name: str | None = None
    data_path: str = Field(min_length=1)
    value: float | tuple[float, float, float] | tuple[float, float, float, float]
    frame: int = Field(ge=-1_048_574, le=1_048_574)
    array_index: int | None = Field(default=None, ge=0, le=3)

    @model_validator(mode="after")
    def validate_constraint_owner(self) -> "CameraKeyframe":
        if (self.owner == "CONSTRAINT") != (self.constraint_name is not None):
            raise ValueError("constraint_name is required only for CONSTRAINT keyframes")
        allowed = {
            "OBJECT": {"location", "rotation_euler", "rotation_quaternion", "scale"},
            "CAMERA_DATA": {"lens", "ortho_scale", "shift_x", "shift_y", "clip_start", "clip_end"},
            "DOF": {"focus_distance", "aperture_fstop"},
            "CONSTRAINT": {"influence", "offset_factor"},
        }
        if self.data_path not in allowed[self.owner]:
            raise ValueError(f"data_path '{self.data_path}' is not allowed for {self.owner}")
        return self


@mcp.tool()
async def keyframe_camera_rig(
    ctx: Context,
    keyframes: Annotated[list[CameraKeyframe], Field(min_length=1, max_length=500)],
    policy: KeyPolicy = "REPLACE",
    interpolation: Interpolation = "BEZIER",
    handle_left: HandleType = "AUTO_CLAMPED",
    handle_right: HandleType = "AUTO_CLAMPED",
) -> dict:
    """Set coordinated allowlisted camera-rig channels without touching unrelated keys."""
    payload = [item.model_dump(exclude_none=True) for item in keyframes]
    return await asyncio.to_thread(
        _call,
        "keyframe_camera_rig",
        {
            "keyframes": payload,
            "policy": policy,
            "interpolation": interpolation,
            "handle_left": handle_left,
            "handle_right": handle_right,
        },
    )


@mcp.tool()
async def set_camera_interpolation(
    ctx: Context,
    object_name: str,
    owner: Literal["OBJECT", "CAMERA_DATA"],
    data_path: str,
    frame_start: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    frame_end: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    array_index: Annotated[int | None, Field(ge=0, le=3)] = None,
    interpolation: Interpolation = "BEZIER",
    handle_left: HandleType = "AUTO_CLAMPED",
    handle_right: HandleType = "AUTO_CLAMPED",
    easing: Literal["AUTO", "EASE_IN", "EASE_OUT", "EASE_IN_OUT"] | None = None,
) -> dict:
    """Change interpolation only on one exact channel and inclusive frame interval."""
    if frame_start > frame_end:
        raise ToolError("frame_start must be less than or equal to frame_end")
    return await asyncio.to_thread(_call, "set_camera_interpolation", _tool_params(locals()), [object_name])


@mcp.tool()
async def create_focus_pull(
    ctx: Context,
    scene_name: str,
    camera_name: str,
    start_frame: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    end_frame: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    start_subject_name: str | None = None,
    start_point: tuple[float, float, float] | None = None,
    end_subject_name: str | None = None,
    end_point: tuple[float, float, float] | None = None,
    mode: FocusPullMode = "DISTANCE",
    interpolation: Interpolation = "BEZIER",
    focus_control_name: str = "MCP Focus Pull",
    collection_name: str = "MCP Camera Controls",
) -> dict:
    """Animate camera-space focus distance or a dedicated live focus control between two subjects."""
    if start_frame >= end_frame:
        raise ToolError("start_frame must be less than end_frame")
    if (start_subject_name is None) == (start_point is None):
        raise ToolError("Supply exactly one start subject or start point")
    if (end_subject_name is None) == (end_point is None):
        raise ToolError("Supply exactly one end subject or end point")
    return await asyncio.to_thread(_call, "create_focus_pull", _tool_params(locals()), [camera_name])


@mcp.tool()
async def create_dolly_zoom(
    ctx: Context,
    scene_name: str,
    camera_name: str,
    movement_object_name: str,
    start_frame: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    end_frame: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    start_distance: Annotated[float, Field(gt=0)],
    end_distance: Annotated[float, Field(gt=0)],
    subject_object_name: str | None = None,
    subject_point: tuple[float, float, float] | None = None,
    subject_reference_size: Annotated[float, Field(gt=0)] = 1.0,
    start_lens: Annotated[float | None, Field(gt=0)] = None,
    framing_axis: FramingAxis = "VERTICAL",
    interpolation: Interpolation = "LINEAR",
) -> dict:
    """Animate a lens/distance pair that approximately preserves an explicit subject reference size."""
    if start_frame >= end_frame:
        raise ToolError("start_frame must be less than end_frame")
    if (subject_object_name is None) == (subject_point is None):
        raise ToolError("Supply exactly one subject_object_name or subject_point")
    return await asyncio.to_thread(
        _call,
        "create_dolly_zoom",
        _tool_params(locals()),
        [camera_name, movement_object_name],
    )


@mcp.tool()
async def add_camera_shake(
    ctx: Context,
    scene_name: str,
    camera_name: str,
    collection_name: str,
    control_name: str,
    frame_start: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    frame_end: Annotated[int, Field(ge=-1_048_574, le=1_048_574)],
    translation_strength: tuple[float, float, float] = (0.02, 0.02, 0.01),
    rotation_strength: tuple[float, float, float] = (0.01, 0.01, 0.02),
    noise_scale: Annotated[float, Field(gt=0)] = 12.0,
    phase: float = 0.0,
    depth: Annotated[int, Field(ge=0, le=8)] = 1,
    influence: Annotated[float, Field(ge=0, le=1)] = 1.0,
) -> dict:
    """Add deterministic procedural shake on a new parent control, preserving authored camera curves."""
    if frame_start >= frame_end:
        raise ToolError("frame_start must be less than frame_end")
    if not any(translation_strength) and not any(rotation_strength):
        raise ToolError("At least one shake strength component must be non-zero")
    return await asyncio.to_thread(_call, "add_camera_shake", _tool_params(locals()), [camera_name])
