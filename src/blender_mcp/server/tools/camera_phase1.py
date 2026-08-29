"""Typed shot, animation, and reusable-camera-rig tools."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..app import mcp
from .camera import ConstraintSpace, FollowForwardAxis, LockAxis, TrackAxis, UpAxis, _call, _dump, _tool_params

AnimationOwner = Literal["OBJECT", "CAMERA_DATA", "CONSTRAINT", "DOF"]
KeyPolicy = Literal["REPLACE", "INSERT_ONLY"]
Interpolation = Literal["CONSTANT", "LINEAR", "BEZIER"]
HandleType = Literal["FREE", "ALIGNED", "VECTOR", "AUTO", "AUTO_CLAMPED"]
FocusPullMode = Literal["DISTANCE", "FOCUS_CONTROL"]
FramingAxis = Literal["HORIZONTAL", "VERTICAL"]
MarkerAction = Literal["LIST", "CREATE", "UPDATE", "REMOVE"]
MatchPolicy = Literal["TRANSFORM_ONLY", "OPTICS_ONLY", "FULL"]
DataPolicy = Literal["COPY", "LINK"]
AnimationPolicy = Literal["COPY", "LINK", "NONE"]
ExternalTargetPolicy = Literal["SHARE", "REJECT"]
CameraConstraint = Literal[
    "TRACK_TO",
    "DAMPED_TRACK",
    "LOCKED_TRACK",
    "FOLLOW_PATH",
    "CHILD_OF",
    "COPY_LOCATION",
    "COPY_ROTATION",
    "COPY_TRANSFORMS",
    "LIMIT_LOCATION",
    "LIMIT_ROTATION",
    "LIMIT_SCALE",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


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


class MarkerEdit(_StrictModel):
    """One exact marker edit; fields are interpreted by the requested action."""

    name: str = Field(min_length=1)
    frame: int | None = Field(default=None, ge=-1_048_574, le=1_048_574)
    camera_name: str | None = None


class WorldTransform(_StrictModel):
    """Complete world transform using a [w, x, y, z] quaternion."""

    location: tuple[float, float, float]
    rotation_quaternion: tuple[float, float, float, float]
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


class RenderGatePatch(_StrictModel):
    resolution_x: int | None = Field(default=None, ge=4, le=65_536)
    resolution_y: int | None = Field(default=None, ge=4, le=65_536)
    resolution_percentage: int | None = Field(default=None, ge=1, le=100)
    pixel_aspect_x: float | None = Field(default=None, gt=0, le=200)
    pixel_aspect_y: float | None = Field(default=None, gt=0, le=200)


class RenderBorderPatch(_StrictModel):
    use_border: bool | None = None
    use_crop_to_border: bool | None = None
    min_x: float | None = Field(default=None, ge=0, le=1)
    max_x: float | None = Field(default=None, ge=0, le=1)
    min_y: float | None = Field(default=None, ge=0, le=1)
    max_y: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_supplied_bounds(self) -> "RenderBorderPatch":
        if self.min_x is not None and self.max_x is not None and self.min_x >= self.max_x:
            raise ValueError("min_x must be less than max_x")
        if self.min_y is not None and self.max_y is not None and self.min_y >= self.max_y:
            raise ValueError("min_y must be less than max_y")
        return self


class SafeAreasPatch(_StrictModel):
    """Normalized title/action safe-area margins."""

    title: tuple[float, float] | None = None
    action: tuple[float, float] | None = None
    title_center: tuple[float, float] | None = None
    action_center: tuple[float, float] | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "SafeAreasPatch":
        for field in ("title", "action", "title_center", "action_center"):
            value = getattr(self, field)
            if value is not None and any(component < 0 or component > 1 for component in value):
                raise ValueError(f"{field} components must be between 0 and 1")
        return self


class CameraGuidesPatch(_StrictModel):
    """Camera guide fields relevant to shot framing."""

    show_safe_areas: bool | None = None
    show_composition_center: bool | None = None
    show_composition_center_diagonal: bool | None = None
    show_composition_golden: bool | None = None
    show_composition_golden_tria_a: bool | None = None
    show_composition_golden_tria_b: bool | None = None
    show_composition_harmony_tri_a: bool | None = None
    show_composition_harmony_tri_b: bool | None = None
    show_composition_thirds: bool | None = None


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


@mcp.tool()
async def create_camera_markers(
    ctx: Context,
    scene_name: str,
    action: MarkerAction,
    markers: Annotated[list[MarkerEdit] | None, Field(max_length=200)] = None,
    replace_existing: bool = False,
) -> dict:
    """List or batch-create/update/remove exact camera-cut markers after full preflight."""
    if action != "LIST" and not markers:
        raise ToolError("markers must not be empty for a mutating action")
    if action == "LIST" and markers:
        raise ToolError("LIST does not accept marker edits")
    payload = [item.model_dump(exclude_none=True) for item in markers or []]
    return await asyncio.to_thread(
        _call,
        "create_camera_markers",
        {"scene_name": scene_name, "action": action, "markers": payload, "replace_existing": replace_existing},
    )


@mcp.tool()
async def match_camera_transform(
    ctx: Context,
    destination_name: str,
    policy: MatchPolicy = "TRANSFORM_ONLY",
    source_object_name: str | None = None,
    world_transform: WorldTransform | None = None,
) -> dict:
    """Match a destination in world space and optionally copy explicit camera optical fields."""
    if (source_object_name is None) == (world_transform is None):
        raise ToolError("Supply exactly one source_object_name or world_transform")
    if policy != "TRANSFORM_ONLY" and source_object_name is None:
        raise ToolError("Optics matching requires a source camera object")
    return await asyncio.to_thread(
        _call,
        "match_camera_transform",
        {
            "destination_name": destination_name,
            "policy": policy,
            "source_object_name": source_object_name,
            "world_transform": _dump(world_transform),
        },
        [destination_name],
    )


@mcp.tool()
async def duplicate_camera_rig(
    ctx: Context,
    scene_name: str,
    source_root_name: str,
    collection_name: str,
    new_rig_name: str,
    camera_data_policy: DataPolicy = "COPY",
    path_data_policy: DataPolicy = "COPY",
    animation_policy: AnimationPolicy = "COPY",
    external_target_policy: ExternalTargetPolicy = "SHARE",
) -> dict:
    """Duplicate one tagged rig and explicitly control datablock, action, and external-target sharing."""
    return await asyncio.to_thread(_call, "duplicate_camera_rig", _tool_params(locals()))


@mcp.tool()
async def add_camera_constraint(
    ctx: Context,
    scene_name: str,
    owner_name: str,
    constraint_name: str,
    constraint_type: CameraConstraint,
    target_name: str | None = None,
    subtarget: str | None = None,
    influence: Annotated[float, Field(ge=0, le=1)] = 1.0,
    owner_space: ConstraintSpace = "WORLD",
    target_space: ConstraintSpace = "WORLD",
    stack_index: Annotated[int, Field(ge=-1)] = -1,
    preserve_transform: bool = True,
    track_axis: TrackAxis = "TRACK_NEGATIVE_Z",
    up_axis: UpAxis = "UP_Y",
    lock_axis: LockAxis = "LOCK_Y",
    forward_axis: FollowForwardAxis = "FORWARD_X",
    use_curve_follow: bool = True,
    use_fixed_location: bool = True,
    offset_factor: Annotated[float, Field(ge=0, le=1)] = 0.0,
    use_x: bool = True,
    use_y: bool = True,
    use_z: bool = True,
    invert_x: bool = False,
    invert_y: bool = False,
    invert_z: bool = False,
    minimum: tuple[float, float, float] | None = None,
    maximum: tuple[float, float, float] | None = None,
) -> dict:
    """Add or update one curated, typed camera-rig constraint with a stable name."""
    targeted = constraint_type not in {"LIMIT_LOCATION", "LIMIT_ROTATION", "LIMIT_SCALE"}
    if targeted != (target_name is not None):
        raise ToolError(
            "This constraint type requires target_name" if targeted else "Limit constraints do not use target_name"
        )
    if constraint_type.startswith("LIMIT_") and minimum is None and maximum is None:
        raise ToolError("Limit constraints require minimum and/or maximum")
    return await asyncio.to_thread(_call, "add_camera_constraint", _tool_params(locals()), [owner_name])


@mcp.tool()
async def configure_camera_render_gate(
    ctx: Context,
    scene_name: str,
    camera_name: str | None = None,
    render: RenderGatePatch | None = None,
    border: RenderBorderPatch | None = None,
    safe_areas: SafeAreasPatch | None = None,
    guides: CameraGuidesPatch | None = None,
) -> dict:
    """Patch the scene render gate and optional camera guides, reporting each old and new value."""
    payloads = [_dump(render), _dump(border), _dump(safe_areas), _dump(guides)]
    if not any(payloads):
        raise ToolError("Provide at least one render-gate field to change")
    if payloads[3] and camera_name is None:
        raise ToolError("camera_name is required when guides are supplied")
    return await asyncio.to_thread(
        _call,
        "configure_camera_render_gate",
        {
            "scene_name": scene_name,
            "camera_name": camera_name,
            "render": payloads[0],
            "border": payloads[1],
            "safe_areas": payloads[2],
            "guides": payloads[3],
        },
        [camera_name] if camera_name else [],
    )


@mcp.tool()
async def validate_camera_rig(
    ctx: Context,
    scene_name: str,
    object_names: Annotated[list[str] | None, Field(max_length=500)] = None,
    sample_frames: Annotated[list[int] | None, Field(max_length=24)] = None,
) -> dict:
    """Read-only structural validation of explicit or scene camera rigs at bounded sample frames."""
    return await asyncio.to_thread(
        _call,
        "validate_camera_rig",
        {"scene_name": scene_name, "object_names": object_names, "sample_frames": sample_frames},
    )
