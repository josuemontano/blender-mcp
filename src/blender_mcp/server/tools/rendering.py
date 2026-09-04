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
    motion_blur: "MotionBlurPatch | None" = None
    film: "FilmPatch | None" = None
    output: "OutputPatch | None" = None
    metadata: "MetadataPatch | None" = None
    multiview: "MultiviewPatch | None" = None
    cycles: "CyclesPatch | None" = None
    eevee: "EeveePatch | None" = None

    @model_validator(mode="after")
    def validate_patch(self) -> "RenderSettingsPatch":
        """Require at least one field and a valid optional frame range."""
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        if self.frame_start is not None and self.frame_end is not None and self.frame_end < self.frame_start:
            raise ValueError("frame_end must be greater than or equal to frame_start")
        return self


class MotionBlurPatch(BaseModel):
    """Engine-independent render motion-blur controls when exposed by Blender RNA."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool | None = None
    shutter: Annotated[float | None, Field(ge=0, le=10)] = None
    position: Literal["START", "CENTER", "END"] | None = None


class FilmPatch(BaseModel):
    """Film/background controls."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    transparent: bool | None = None
    transparent_glass: bool | None = None
    transparent_roughness: Annotated[float | None, Field(ge=0, le=1)] = None


class OutputPatch(BaseModel):
    """Output path and image-format controls; no render is started."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    filepath: str | None = None
    image_format: Literal["PNG", "JPEG", "OPEN_EXR", "OPEN_EXR_MULTILAYER", "TIFF", "WEBP"] | None = None
    color_mode: Literal["BW", "RGB", "RGBA"] | None = None
    color_depth: Literal["8", "16", "32"] | None = None
    compression: Annotated[int | None, Field(ge=0, le=100)] = None
    quality: Annotated[int | None, Field(ge=0, le=100)] = None
    exr_codec: Literal["NONE", "PXR24", "ZIP", "PIZ", "RLE", "ZIPS", "B44", "B44A", "DWAA", "DWAB"] | None = None
    use_file_extension: bool | None = None
    use_overwrite: bool | None = None
    use_placeholder: bool | None = None


class MetadataPatch(BaseModel):
    """Render stamp/metadata controls."""

    model_config = ConfigDict(extra="forbid")
    use_stamp: bool | None = None
    use_stamp_date: bool | None = None
    use_stamp_time: bool | None = None
    use_stamp_render_time: bool | None = None
    use_stamp_frame: bool | None = None
    use_stamp_frame_range: bool | None = None
    use_stamp_camera: bool | None = None
    use_stamp_scene: bool | None = None
    use_stamp_note: bool | None = None
    stamp_note_text: str | None = None


class MultiviewPatch(BaseModel):
    """Stereo/multiview output controls."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    views_format: Literal["INDIVIDUAL", "STEREO_3D"] | None = None
    stereo_3d_format: Literal["ANAGLYPH", "INTERLACE", "TIMESEQUENTIAL", "SIDEBYSIDE", "TOPBOTTOM"] | None = None


class CyclesPatch(BaseModel):
    """Cycles-only sampling and denoising controls."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    samples: Annotated[int | None, Field(ge=1, le=1_000_000)] = None
    preview_samples: Annotated[int | None, Field(ge=1, le=1_000_000)] = None
    use_adaptive_sampling: bool | None = None
    adaptive_threshold: Annotated[float | None, Field(gt=0, le=1)] = None
    time_limit: Annotated[float | None, Field(ge=0, le=604_800)] = None
    use_denoising: bool | None = None
    denoiser: Literal["OPENIMAGEDENOISE", "OPTIX"] | None = None


class EeveePatch(BaseModel):
    """EEVEE-only sampling controls, resolved against Blender 5.x RNA at runtime."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    taa_samples: Annotated[int | None, Field(ge=1, le=1_000_000)] = None
    taa_render_samples: Annotated[int | None, Field(ge=1, le=1_000_000)] = None
    use_shadows: bool | None = None


RenderSettingsPatch.model_rebuild()


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
async def inspect_render_setup(
    ctx: Context,
    scene_name: str | None = None,
    graph_sections: list[Literal["NODES", "LINKS", "DEPENDENCIES"]] | None = None,
    limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """Inspect render engine, output, color, camera, view layers, passes, and compositor state."""
    return await _call(
        "inspect_render_setup",
        {"scene_name": scene_name, "graph_sections": graph_sections, "limit": limit, "offset": offset},
    )


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
    render_slot_policy: Literal["USE_ACTIVE", "NEW_SLOT", "REPLACE_ACTIVE"] = "USE_ACTIVE",
    verify_outputs: bool = True,
    verify_passes: bool = True,
    max_duration_seconds: Annotated[float | None, Field(gt=0, le=604_800)] = None,
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
            "render_slot_policy": render_slot_policy,
            "verify_outputs": verify_outputs,
            "verify_passes": verify_passes,
            "max_duration_seconds": max_duration_seconds,
        },
    )
