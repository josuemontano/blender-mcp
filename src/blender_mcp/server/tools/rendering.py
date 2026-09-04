# ruff: file-ignore[docstring-missing-exception, docstring-missing-returns, too-many-arguments, too-many-positional-arguments, unused-function-argument]
"""Typed tools for scene render configuration, view layers, passes, and rendering."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..app import mcp
from ..connection import get_blender_connection
from .envelope import ok


class RenderSettingsPatch(BaseModel):
    """Validated patch for common scene render settings."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    engine: Literal["BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH", "CYCLES"] | None = None
    resolution_x: Annotated[int | None, Field(ge=4, le=65_536)] = None
    resolution_y: Annotated[int | None, Field(ge=4, le=65_536)] = None
    resolution_percentage: Annotated[int | None, Field(ge=1, le=100)] = None
    pixel_aspect_x: Annotated[float | None, Field(gt=0, le=200)] = None
    pixel_aspect_y: Annotated[float | None, Field(gt=0, le=200)] = None
    fps: Annotated[int | None, Field(ge=1, le=960)] = None
    fps_base: Annotated[float | None, Field(gt=0, le=1000)] = None
    frame_start: int | None = None
    frame_end: int | None = None
    frame_step: Annotated[int | None, Field(ge=1)] = None
    film_transparent: bool | None = None
    image_format: Literal["PNG", "JPEG", "OPEN_EXR", "TIFF", "WEBP"] | None = None
    color_mode: Literal["BW", "RGB", "RGBA"] | None = None
    color_depth: Literal["8", "16", "32"] | None = None
    compression: Annotated[int | None, Field(ge=0, le=100)] = None
    quality: Annotated[int | None, Field(ge=0, le=100)] = None
    cycles_samples: Annotated[int | None, Field(ge=1, le=16_384)] = None
    cycles_use_denoising: bool | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "RenderSettingsPatch":
        """Require at least one field and a valid optional frame range."""
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        if self.frame_start is not None and self.frame_end is not None and self.frame_end < self.frame_start:
            raise ValueError("frame_end must be greater than or equal to frame_start")
        return self


class ViewLayerPatch(BaseModel):
    """Validated view-layer visibility and render-pass patch."""

    model_config = ConfigDict(extra="forbid")

    use: bool | None = None
    use_sky: bool | None = None
    use_solid: bool | None = None
    use_strand: bool | None = None
    material_override: str | None = None
    world_override: str | None = None
    use_pass_combined: bool | None = None
    use_pass_z: bool | None = None
    use_pass_mist: bool | None = None
    use_pass_normal: bool | None = None
    use_pass_position: bool | None = None
    use_pass_vector: bool | None = None
    use_pass_uv: bool | None = None
    use_pass_object_index: bool | None = None
    use_pass_material_index: bool | None = None
    use_pass_cryptomatte_object: bool | None = None
    use_pass_cryptomatte_material: bool | None = None
    use_pass_cryptomatte_asset: bool | None = None
    pass_cryptomatte_depth: Annotated[int | None, Field(ge=2, le=16, multiple_of=2)] = None

    @model_validator(mode="after")
    def require_field(self) -> "ViewLayerPatch":
        """Reject empty patches."""
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        return self


async def _call(command: str, params: dict, *, changed_resources: list[str] | None = None) -> dict:
    result = await asyncio.to_thread(get_blender_connection().send_command, command, params)
    resources = changed_resources or []
    if isinstance(result, dict):
        result = dict(result)
        resources = result.pop("changed_resources", resources)
    return ok(result, changed_resources=resources)


@mcp.tool()
async def inspect_render_setup(ctx: Context, scene_name: str | None = None) -> dict:
    """Inspect render engine, output, color, camera, view layers, passes, and compositor state."""
    return await _call("inspect_render_setup", {"scene_name": scene_name})


@mcp.tool()
async def configure_render_settings(ctx: Context, scene_name: str, patch: RenderSettingsPatch) -> dict:
    """Patch validated scene render settings without rendering or writing a file."""
    return await _call(
        "configure_render_settings",
        {"scene_name": scene_name, "patch": patch.model_dump(exclude_none=True)},
        changed_resources=[scene_name],
    )


@mcp.tool()
async def manage_view_layers(
    ctx: Context,
    scene_name: str,
    action: Literal["CREATE", "PATCH", "REMOVE"],
    view_layer_name: str,
    patch: ViewLayerPatch | None = None,
    confirm_remove: bool = False,
) -> dict:
    """Create, patch, or explicitly remove one view layer and its render-pass settings."""
    if action == "PATCH" and patch is None:
        raise ToolError("PATCH requires patch")
    if action == "REMOVE" and patch is not None:
        raise ToolError("REMOVE does not accept patch")
    if action == "REMOVE" and not confirm_remove:
        raise ToolError("confirm_remove=True is required for REMOVE")
    return await _call(
        "manage_view_layers",
        {
            "scene_name": scene_name,
            "action": action,
            "view_layer_name": view_layer_name,
            "patch": patch.model_dump(exclude_none=True) if patch else None,
            "confirm_remove": confirm_remove,
        },
        changed_resources=[view_layer_name],
    )


@mcp.tool()
async def render_scene(
    ctx: Context,
    scene_name: str,
    filepath: Annotated[str, Field(min_length=1)],
    mode: Literal["STILL", "ANIMATION"] = "STILL",
    view_layer_name: str | None = None,
    frame: int | None = None,
    max_animation_frames: Annotated[int, Field(ge=1, le=10_000)] = 250,
    confirm_render: bool = False,
    confirm_overwrite: bool = False,
) -> dict:
    """Render a still or bounded animation to an explicit path after confirmation."""
    if not confirm_render:
        raise ToolError("confirm_render=True is required")
    return await _call(
        "render_scene",
        {
            "scene_name": scene_name,
            "filepath": filepath,
            "mode": mode,
            "view_layer_name": view_layer_name,
            "frame": frame,
            "max_animation_frames": max_animation_frames,
            "confirm_render": confirm_render,
            "confirm_overwrite": confirm_overwrite,
        },
    )
