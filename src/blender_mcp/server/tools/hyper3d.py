"""Hyper3D Rodin AI 3D-generation integration tools."""

import base64
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")


def _process_bbox(
    original_bbox: tuple[float, float, float] | list[float] | list[int] | None,
) -> list[int] | None:
    if original_bbox is None:
        return None
    if any(i <= 0 for i in original_bbox):
        raise ValueError("Incorrect number range: bbox must be bigger than zero!")
    if all(isinstance(i, int) for i in original_bbox):
        return list(original_bbox)
    return [int(float(i) / max(original_bbox) * 100) for i in original_bbox] if original_bbox else None


@mcp.tool()
async def generate_hyper3d_model_via_text(
    ctx: Context,
    text_prompt: str,
    bbox_condition: tuple[float, float, float] | None = None,
) -> dict:
    """Generate 3D asset using Hyper3D by giving description of the desired asset, and import the asset into Blender.

    The 3D asset has built-in materials.
    The generated model has a normalized size, so re-scaling after generation can be useful.

    Args:
        ctx: MCP request context.
        text_prompt: A short description of the desired model in **English**.
        bbox_condition: Optional [Length, Width, Height] ratio for the model.

    Returns:
        the submitted job's task_uuid and subscription_key (or an error if submission fails).
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "create_rodin_job",
            {
                "text_prompt": text_prompt,
                "images": None,
                "bbox_condition": _process_bbox(bbox_condition),
            },
        )
        if not result.get("submit_time", False):
            raise ToolError(f"Hyper3D job submission failed: {result}")
        return ok(
            {
                "task_uuid": result["uuid"],
                "subscription_key": result["jobs"]["subscription_key"],
            }
        )
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {e}")
        raise ToolError(f"Error generating Hyper3D task: {e}") from e


@mcp.tool()
async def generate_hyper3d_model_via_images(
    ctx: Context,
    input_image_paths: list[str] | None = None,
    input_image_urls: list[str] | None = None,
    bbox_condition: tuple[float, float, float] | None = None,
) -> dict:
    """Generate 3D asset using Hyper3D by giving images of the wanted asset, and import the generated asset into Blender.

    The 3D asset has built-in materials.
    The generated model has a normalized size, so re-scaling after generation can be useful.

    Args:
        ctx: MCP request context.
        input_image_paths: The **absolute** paths of input images. Even if only one image is provided, wrap it into a list. Required if Hyper3D Rodin in MAIN_SITE mode.
        input_image_urls: The URLs of input images. Even if only one image is provided, wrap it into a list. Required if Hyper3D Rodin in FAL_AI mode.
        bbox_condition: Optional [Length, Width, Height] ratio for the model. Only one of {input_image_paths, input_image_urls} should be given at a time, depending on the Hyper3D Rodin's current mode.

    Returns:
        the submitted job's task_uuid and subscription_key.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    if input_image_paths is not None and input_image_urls is not None:
        raise ToolError("Conflicting parameters given: pass only one of input_image_paths, input_image_urls.")
    if input_image_paths is None and input_image_urls is None:
        raise ToolError("No image given: pass input_image_paths or input_image_urls.")
    if input_image_paths is not None:
        if not all(os.path.exists(i) for i in input_image_paths):
            raise ToolError("Not all image paths are valid.")
        images = []
        for path in input_image_paths:
            with open(path, "rb") as f:
                images.append((Path(path).suffix, base64.b64encode(f.read()).decode("ascii")))
    else:
        if not all(urlparse(i).scheme for i in input_image_urls):
            raise ToolError("Not all image URLs are valid.")
        images = input_image_urls.copy()
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "create_rodin_job",
            {
                "text_prompt": None,
                "images": images,
                "bbox_condition": _process_bbox(bbox_condition),
            },
        )
        if not result.get("submit_time", False):
            raise ToolError(f"Hyper3D job submission failed: {result}")
        return ok(
            {
                "task_uuid": result["uuid"],
                "subscription_key": result["jobs"]["subscription_key"],
            }
        )
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {e}")
        raise ToolError(f"Error generating Hyper3D task: {e}") from e


@mcp.tool()
async def poll_rodin_job_status(
    ctx: Context,
    subscription_key: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Check if the Hyper3D Rodin generation task is completed.

    For Hyper3D Rodin mode MAIN_SITE:

    Args:
        ctx: MCP request context.
        subscription_key: The subscription_key given in the generate model step. Returns a list of status. The task is done if all status are "Done". If "Failed" showed up, the generating process failed. This is a polling API, so only proceed if the status are finally determined ("Done" or "Canceled"). For Hyper3D Rodin mode FAL_AI:
        request_id: The request_id given in the generate model step.

    Returns:
        the generation task status. The task is done if status is "COMPLETED". The task is in progress if status is "IN_PROGRESS". If status other than "COMPLETED", "IN_PROGRESS", "IN_QUEUE" showed up, the generating process might be failed. This is a polling API, so only proceed if the status are finally determined ("COMPLETED" or some failed state).
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        kwargs = {}
        if subscription_key:
            kwargs = {
                "subscription_key": subscription_key,
            }
        elif request_id:
            kwargs = {
                "request_id": request_id,
            }
        result = blender.send_command("poll_rodin_job_status", kwargs)
        return ok(result)
    except Exception as e:
        logger.error(f"Error polling Hyper3D job status: {e}")
        raise ToolError(f"Error polling Hyper3D job status: {e}") from e


@mcp.tool()
async def import_generated_asset(
    ctx: Context,
    name: str,
    task_uuid: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Import the asset generated by Hyper3D Rodin after the generation task is completed.

    Args:
        ctx: MCP request context.
        name: The name of the object in scene
        task_uuid: For Hyper3D Rodin mode MAIN_SITE: The task_uuid given in the generate model step.
        request_id: For Hyper3D Rodin mode FAL_AI: The request_id given in the generate model step. Only give one of {task_uuid, request_id} based on the Hyper3D Rodin Mode!

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        kwargs = {"name": name}
        if task_uuid:
            kwargs["task_uuid"] = task_uuid
        elif request_id:
            kwargs["request_id"] = request_id
        result = blender.send_command("import_generated_asset", kwargs)
        return ok(result, changed_objects=[name])
    except Exception as e:
        logger.error(f"Error importing Hyper3D asset: {e}")
        raise ToolError(f"Error importing Hyper3D asset: {e}") from e
