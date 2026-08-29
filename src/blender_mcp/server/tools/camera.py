# MCP camera tools intentionally use explicit keyword arguments so agents receive
# a precise schema instead of an unsafe generic RNA property bag.
"""Typed production tools for Blender cameras and editable camera rigs."""

import asyncio
import logging

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

Projection = Literal["PERSP", "ORTHO", "PANO"]
SensorFit = Literal["AUTO", "HORIZONTAL", "VERTICAL"]
AimMode = Literal["IMMEDIATE", "CONSTRAINT"]
TrackingConstraint = Literal["TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK"]
FramePolicy = Literal["MOVE_CAMERA", "CHANGE_LENS", "CHANGE_ORTHO_SCALE"]
SplineType = Literal["BEZIER", "NURBS"]
TrackAxis = Literal[
    "TRACK_X",
    "TRACK_Y",
    "TRACK_Z",
    "TRACK_NEGATIVE_X",
    "TRACK_NEGATIVE_Y",
    "TRACK_NEGATIVE_Z",
]
UpAxis = Literal["UP_X", "UP_Y", "UP_Z"]
LockAxis = Literal["LOCK_X", "LOCK_Y", "LOCK_Z"]
ConstraintSpace = Literal["WORLD", "CUSTOM", "POSE", "LOCAL_WITH_PARENT", "LOCAL"]
FollowForwardAxis = Literal[
    "FORWARD_X",
    "FORWARD_Y",
    "FORWARD_Z",
    "TRACK_NEGATIVE_X",
    "TRACK_NEGATIVE_Y",
    "TRACK_NEGATIVE_Z",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CameraOpticsPatch(_StrictModel):
    """Allowlisted Blender 5.1 camera projection and optical fields."""

    projection: Projection | None = None
    lens: float | None = Field(default=None, gt=0)
    ortho_scale: float | None = Field(default=None, gt=0)
    sensor_width: float | None = Field(default=None, gt=0)
    sensor_height: float | None = Field(default=None, gt=0)
    sensor_fit: SensorFit | None = None
    shift_x: float | None = None
    shift_y: float | None = None
    clip_start: float | None = Field(default=None, gt=0)
    clip_end: float | None = Field(default=None, gt=0)
    panorama_type: str | None = None

    @model_validator(mode="after")
    def validate_clip_order(self) -> "CameraOpticsPatch":
        if self.clip_start is not None and self.clip_end is not None and self.clip_start >= self.clip_end:
            raise ValueError("clip_start must be less than clip_end")
        return self


class CameraDisplayPatch(_StrictModel):
    """Allowlisted Blender 5.1 camera viewport and composition-guide fields."""

    passepartout_alpha: float | None = Field(default=None, ge=0, le=1)
    show_passepartout: bool | None = None
    show_safe_areas: bool | None = None
    show_name: bool | None = None
    show_limits: bool | None = None
    show_mist: bool | None = None
    show_composition_center: bool | None = None
    show_composition_center_diagonal: bool | None = None
    show_composition_golden: bool | None = None
    show_composition_golden_tria_a: bool | None = None
    show_composition_golden_tria_b: bool | None = None
    show_composition_harmony_tri_a: bool | None = None
    show_composition_harmony_tri_b: bool | None = None
    show_composition_thirds: bool | None = None


class CameraDofPatch(_StrictModel):
    """Photographic depth-of-field settings; focus intent is supplied separately."""

    use_dof: bool | None = None
    aperture_fstop: float | None = Field(default=None, gt=0)
    aperture_blades: int | None = Field(default=None, ge=0, le=16)
    aperture_rotation: float | None = None
    aperture_ratio: float | None = Field(default=None, gt=0)


def _dump(model: BaseModel | None) -> dict | None:
    return model.model_dump(exclude_none=True, exclude_unset=True) if model is not None else None


def _tool_params(values: dict) -> dict:
    """Remove FastMCP's context-only argument from a local tool payload."""
    return {key: value for key, value in values.items() if key != "ctx"}


def _call(command: str, params: dict, changed_objects: list[str] | None = None) -> dict:
    try:
        result = get_blender_connection().send_command(command, params)
        changed = result.get("changed_objects", changed_objects or []) if isinstance(result, dict) else changed_objects
        resources = result.get("changed_resources", []) if isinstance(result, dict) else []
        if isinstance(result, dict):
            result = {
                key: value for key, value in result.items() if key not in {"changed_objects", "changed_resources"}
            }
        return ok(result, changed_objects=changed or [], changed_resources=resources)
    except Exception as exc:
        logger.error("Error running %s: %s", command, exc)
        raise ToolError(f"Error running {command}: {exc}") from exc


@mcp.tool()
async def get_camera_rig_info(
    ctx: Context,
    scene_name: str,
    object_name: str,
    descendant_depth: Annotated[int, Field(ge=0, le=12)] = 4,
    child_limit: Annotated[int, Field(ge=1, le=200)] = 50,
    child_offset: Annotated[int, Field(ge=0, le=1999)] = 0,
    animation_limit: Annotated[int, Field(ge=1, le=500)] = 100,
    animation_offset: Annotated[int, Field(ge=0, le=4999)] = 0,
) -> dict:
    """Inspect one camera or rig root before editing it.

    The result labels local and world transforms separately and includes camera optics, DOF,
    constraints, drivers, actions, render gate, active-camera state, camera markers, rig metadata,
    and a bounded descendant page. Continue pages with the returned next offsets. This tool never
    evaluates another frame and never changes the scene.
    """
    return await asyncio.to_thread(
        _call,
        "get_camera_rig_info",
        {
            "scene_name": scene_name,
            "object_name": object_name,
            "descendant_depth": descendant_depth,
            "child_limit": child_limit,
            "child_offset": child_offset,
            "animation_limit": animation_limit,
            "animation_offset": animation_offset,
        },
    )


@mcp.tool()
async def create_camera(
    ctx: Context,
    scene_name: str,
    collection_name: str,
    name: str,
    projection: Projection = "PERSP",
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation_euler: tuple[float, float, float] | None = None,
    rotation_quaternion: tuple[float, float, float, float] | None = None,
    look_at_object_name: str | None = None,
    look_at_point: tuple[float, float, float] | None = None,
    optics: CameraOpticsPatch | None = None,
    make_active: bool = False,
) -> dict:
    """Create a collision-safe camera in an explicit scene collection.

    Coordinates are world-space; Euler angles are XYZ radians and quaternions are [w, x, y, z].
    Supply at most one of Euler rotation, quaternion rotation, look-at object, or look-at point.
    The camera is not selected and does not become the scene camera unless ``make_active`` is true.
    Panoramic settings are capability-checked against the running Blender build.
    """
    orientations = [rotation_euler, rotation_quaternion, look_at_object_name, look_at_point]
    if sum(value is not None for value in orientations) > 1:
        raise ToolError("Supply only one orientation source: Euler, quaternion, look-at object, or look-at point")
    if optics is not None and optics.projection is not None and optics.projection != projection:
        raise ToolError("projection conflicts with optics.projection; supply projection in only one place")
    return await asyncio.to_thread(
        _call,
        "create_camera",
        {
            "scene_name": scene_name,
            "collection_name": collection_name,
            "name": name,
            "projection": projection,
            "location": location,
            "rotation_euler": rotation_euler,
            "rotation_quaternion": rotation_quaternion,
            "look_at_object_name": look_at_object_name,
            "look_at_point": look_at_point,
            "optics": _dump(optics),
            "make_active": make_active,
        },
    )


@mcp.tool()
async def configure_camera(
    ctx: Context,
    camera_name: str,
    optics: CameraOpticsPatch | None = None,
    display: CameraDisplayPatch | None = None,
) -> dict:
    """Patch only the supplied optical, clipping, and viewport fields on one camera.

    This does not change render resolution because the render gate belongs to the scene. The result
    reports old and new values. Use ``configure_camera_dof`` for focus and aperture controls.
    """
    optics_payload = _dump(optics)
    display_payload = _dump(display)
    if not optics_payload and not display_payload:
        raise ToolError("Provide at least one optics or display field to change")
    return await asyncio.to_thread(
        _call,
        "configure_camera",
        {"camera_name": camera_name, "optics": optics_payload, "display": display_payload},
        [camera_name],
    )


@mcp.tool()
async def set_scene_camera(
    ctx: Context,
    scene_name: str,
    camera_name: str,
    marker_name: str | None = None,
    marker_frame: Annotated[int | None, Field(ge=-1_048_574, le=1_048_574)] = None,
    replace_marker: bool = False,
) -> dict:
    """Set the scene camera and optionally bind it to one exact timeline marker.

    ``marker_name`` and ``marker_frame`` must be supplied together. Existing marker bindings are
    preserved unless ``replace_marker`` is true; this avoids silently changing editorial cuts.
    """
    if (marker_name is None) != (marker_frame is None):
        raise ToolError("marker_name and marker_frame must be supplied together")
    return await asyncio.to_thread(
        _call,
        "set_scene_camera",
        {
            "scene_name": scene_name,
            "camera_name": camera_name,
            "marker_name": marker_name,
            "marker_frame": marker_frame,
            "replace_marker": replace_marker,
        },
        [camera_name],
    )


@mcp.tool()
async def aim_camera(
    ctx: Context,
    scene_name: str,
    camera_name: str,
    mode: AimMode = "IMMEDIATE",
    target_object_name: str | None = None,
    target_point: tuple[float, float, float] | None = None,
    subtarget: str | None = None,
    controls_collection_name: str = "MCP Camera Controls",
    constraint_name: str = "MCP Aim",
    constraint_type: TrackingConstraint = "DAMPED_TRACK",
    track_axis: TrackAxis = "TRACK_NEGATIVE_Z",
    up_axis: UpAxis = "UP_Y",
    lock_axis: LockAxis = "LOCK_Y",
    influence: Annotated[float, Field(ge=0, le=1)] = 1.0,
    owner_space: ConstraintSpace = "WORLD",
    target_space: ConstraintSpace = "WORLD",
    stack_index: Annotated[int, Field(ge=-1)] = -1,
) -> dict:
    """Aim a camera once or maintain a live tracking relationship.

    Supply exactly one world-space target source. Immediate mode rotates the camera with local -Z
    toward the target and local Y as up, correctly resolving parent space. Constraint mode updates
    only the named tracking constraint. A point target creates a tagged Empty because Blender live
    constraints require an object target; its returned name is a retained rig dependency.
    """
    if (target_object_name is None) == (target_point is None):
        raise ToolError("Supply exactly one of target_object_name or target_point")
    if subtarget is not None and target_object_name is None:
        raise ToolError("subtarget requires target_object_name")
    return await asyncio.to_thread(
        _call,
        "aim_camera",
        {
            "scene_name": scene_name,
            "camera_name": camera_name,
            "mode": mode,
            "target_object_name": target_object_name,
            "target_point": target_point,
            "subtarget": subtarget,
            "controls_collection_name": controls_collection_name,
            "constraint_name": constraint_name,
            "constraint_type": constraint_type,
            "track_axis": track_axis,
            "up_axis": up_axis,
            "lock_axis": lock_axis,
            "influence": influence,
            "owner_space": owner_space,
            "target_space": target_space,
            "stack_index": stack_index,
        },
        [camera_name],
    )


@mcp.tool()
async def create_camera_target(
    ctx: Context,
    scene_name: str,
    collection_name: str,
    name: str,
    location: tuple[float, float, float] | None = None,
    target_object_name: str | None = None,
    use_evaluated_bounds_center: bool = True,
    reuse: bool = False,
    camera_names: list[str] | None = None,
    constraint_type: TrackingConstraint = "DAMPED_TRACK",
) -> dict:
    """Create or explicitly reuse a tagged Empty as a camera aim control.

    Supply either a world location or an object whose evaluated bounds center should be used. Reuse
    is opt-in and accepts only an Empty already tagged as a camera target. Named cameras receive a
    live -Z tracking constraint; unrelated constraints remain untouched.
    """
    if (location is None) == (target_object_name is None):
        raise ToolError("Supply exactly one of location or target_object_name")
    return await asyncio.to_thread(
        _call,
        "create_camera_target",
        {
            "scene_name": scene_name,
            "collection_name": collection_name,
            "name": name,
            "location": location,
            "target_object_name": target_object_name,
            "use_evaluated_bounds_center": use_evaluated_bounds_center,
            "reuse": reuse,
            "camera_names": camera_names or [],
            "constraint_type": constraint_type,
        },
    )


@mcp.tool()
async def frame_camera_on_objects(
    ctx: Context,
    scene_name: str,
    camera_name: str,
    object_names: list[str],
    margin: Annotated[float, Field(ge=0, lt=0.9)] = 0.1,
    policy: FramePolicy = "MOVE_CAMERA",
    aim_at_center: bool = True,
) -> dict:
    """Fit explicit evaluated objects in a camera without viewport operators.

    ``MOVE_CAMERA`` preserves perspective optics, ``CHANGE_LENS`` preserves camera position, and
    ``CHANGE_ORTHO_SCALE`` is required for orthographic scale changes. Modifier-evaluated world
    bounds, target point, solved distance or optical value, and limiting frame axis are returned.
    The margin is the fractional inset on each side of the render frame.
    """
    if not object_names:
        raise ToolError("object_names must not be empty")
    return await asyncio.to_thread(
        _call,
        "frame_camera_on_objects",
        {
            "scene_name": scene_name,
            "camera_name": camera_name,
            "object_names": object_names,
            "margin": margin,
            "policy": policy,
            "aim_at_center": aim_at_center,
        },
        [camera_name],
    )


@mcp.tool()
async def create_orbit_camera_rig(
    ctx: Context,
    scene_name: str,
    collection_name: str,
    rig_name: str,
    pivot: tuple[float, float, float],
    radius: Annotated[float, Field(gt=0)],
    azimuth: float = 0.0,
    elevation: float = 0.0,
    roll: float = 0.0,
    lens: Annotated[float, Field(gt=0)] = 50.0,
    target_height: float = 0.0,
) -> dict:
    """Build a new editable orbit rig from standard Empty, Camera, and Damped Track objects.

    Angles are radians. The root Z rotation is azimuth; the boom offset encodes radius/elevation;
    camera roll remains available on the camera control. All members are tagged with one rig UUID,
    role, schema version, and owner so agents can inspect or duplicate them safely.
    """
    return await asyncio.to_thread(_call, "create_orbit_camera_rig", _tool_params(locals()))


@mcp.tool()
async def create_dolly_camera_rig(
    ctx: Context,
    scene_name: str,
    collection_name: str,
    rig_name: str,
    location: tuple[float, float, float],
    rail_direction: tuple[float, float, float] = (0.0, 1.0, 0.0),
    yaw: float = 0.0,
    camera_height: Annotated[float, Field(ge=0)] = 1.5,
    target_distance: Annotated[float, Field(gt=0)] = 10.0,
    lens: Annotated[float, Field(gt=0)] = 50.0,
    create_target: bool = True,
) -> dict:
    """Build a conventional dolly rig with root, camera-height control, camera, and optional aim target.

    ``rail_direction`` is a non-zero local direction exposed as rig metadata for animation planning;
    translate/yaw the root for the dolly move and animate the child control for height/pitch/roll.
    """
    return await asyncio.to_thread(_call, "create_dolly_camera_rig", _tool_params(locals()))


@mcp.tool()
async def create_crane_camera_rig(
    ctx: Context,
    scene_name: str,
    collection_name: str,
    rig_name: str,
    location: tuple[float, float, float],
    base_height: Annotated[float, Field(ge=0)] = 1.0,
    arm_length: Annotated[float, Field(gt=0)] = 5.0,
    elevation: float = 0.0,
    pan: float = 0.0,
    tilt: float = 0.0,
    roll: float = 0.0,
    lens: Annotated[float, Field(gt=0)] = 50.0,
    create_target: bool = True,
) -> dict:
    """Build an editable crane hierarchy with base, arm pivot, boom, head, camera, and optional target.

    Angles are radians and remain independently animatable on standard object transforms. The boom
    length is its local X offset; no opaque driver or optional add-on dependency is introduced.
    """
    return await asyncio.to_thread(_call, "create_crane_camera_rig", _tool_params(locals()))


@mcp.tool()
async def create_camera_path_rig(
    ctx: Context,
    scene_name: str,
    collection_name: str,
    rig_name: str,
    camera_name: str,
    curve_object_name: str | None = None,
    path_points: list[tuple[float, float, float]] | None = None,
    spline_type: SplineType = "BEZIER",
    forward_axis: FollowForwardAxis = "TRACK_NEGATIVE_Z",
    up_axis: UpAxis = "UP_Y",
    use_curve_follow: bool = True,
    start_frame: Annotated[int | None, Field(ge=-1_048_574, le=1_048_574)] = None,
    end_frame: Annotated[int | None, Field(ge=-1_048_574, le=1_048_574)] = None,
    target_object_name: str | None = None,
) -> dict:
    """Attach an existing camera to a new rig root following one explicit curve.

    Supply either an existing curve name or at least two world-space points for a new Bézier/NURBS
    path. Optional start/end frames key the constraint's fixed-position ``offset_factor`` from 0 to
    1 without touching curve path animation. The camera's pre-rig world transform is preserved.
    """
    if (curve_object_name is None) == (path_points is None):
        raise ToolError("Supply exactly one of curve_object_name or path_points")
    if path_points is not None and len(path_points) < 2:
        raise ToolError("path_points must contain at least two points")
    if (start_frame is None) != (end_frame is None):
        raise ToolError("start_frame and end_frame must be supplied together")
    if start_frame is not None and end_frame is not None and start_frame >= end_frame:
        raise ToolError("start_frame must be less than end_frame")
    return await asyncio.to_thread(_call, "create_camera_path_rig", _tool_params(locals()))


@mcp.tool()
async def configure_camera_dof(
    ctx: Context,
    scene_name: str,
    camera_name: str,
    patch: CameraDofPatch,
    focus_object_name: str | None = None,
    focus_distance: Annotated[float | None, Field(gt=0)] = None,
    focus_point: tuple[float, float, float] | None = None,
    focus_target_name: str | None = None,
    focus_collection_name: str = "MCP Camera Controls",
    reuse_focus_target: bool = False,
) -> dict:
    """Configure photographic depth of field without changing camera aim.

    Supply at most one focus intent: an existing object, a positive distance, or a world-space point.
    A point creates (or explicitly reuses) a tagged focus Empty. Object focus and aim targets remain
    separate dependencies. The visible result still depends on the render engine and sampling.
    """
    if sum(value is not None for value in (focus_object_name, focus_distance, focus_point)) > 1:
        raise ToolError("Supply at most one focus intent: focus object, focus distance, or focus point")
    if focus_point is not None and not focus_target_name:
        raise ToolError("focus_target_name is required when focus_point is supplied")
    patch_payload = _dump(patch)
    if not patch_payload and focus_object_name is None and focus_distance is None and focus_point is None:
        raise ToolError("Provide at least one depth-of-field or focus change")
    return await asyncio.to_thread(
        _call,
        "configure_camera_dof",
        {
            "scene_name": scene_name,
            "camera_name": camera_name,
            "patch": patch_payload,
            "focus_object_name": focus_object_name,
            "focus_distance": focus_distance,
            "focus_point": focus_point,
            "focus_target_name": focus_target_name,
            "focus_collection_name": focus_collection_name,
            "reuse_focus_target": reuse_focus_target,
        },
        [camera_name],
    )
