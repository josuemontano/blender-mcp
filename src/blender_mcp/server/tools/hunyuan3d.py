"""Hunyuan3D AI 3D-generation integration tools."""

import logging

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
async def generate_hunyuan3d_model(
    ctx: Context,
    text_prompt: str | None = None,
    input_image_url: str | None = None,
) -> dict:
    """Submit a Hunyuan3D text- and/or image-to-3D generation job.

    This call only starts the asynchronous job. Poll it with `poll_hunyuan_job_status`,
    then import a completed result with `import_generated_asset_hunyuan`. Generated
    assets include materials.

    Args:
        ctx: MCP request context.
        text_prompt: (Optional) A short description of the desired model in English/Chinese.
        input_image_url: Optional local path or remote URL of a reference image. Omit it for text-only generation.

    Returns:
        a job_id (format: "job_xxx") indicating the task is in progress; poll with poll_hunyuan_job_status.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "create_hunyuan_job",
            {
                "text_prompt": text_prompt,
                "image": input_image_url,
            },
        )
        if "JobId" in result.get("Response", {}):
            job_id = result["Response"]["JobId"]
            return ok({"job_id": f"job_{job_id}"})
        return ok(result)
    except Exception as e:
        logger.error(f"Error generating Hunyuan3D task: {e}")
        raise ToolError(f"Error generating Hunyuan3D task: {e}") from e


@mcp.tool()
def poll_hunyuan_job_status(
    ctx: Context,
    job_id: str | None = None,
) -> dict:
    """Get the current status and downloadable files for a Hunyuan3D generation job.

    Poll until the job reaches a terminal status. On `DONE`, choose a `ResultFile3Ds`
    URL (prefer `.glb`) and pass it to `import_generated_asset_hunyuan`.

    Args:
        ctx: MCP request context.
        job_id: Job ID returned by `generate_hunyuan3d_model`.

    Returns:
        the provider status and, on success, one or more downloadable model URLs.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("poll_hunyuan_job_status", {"job_id": job_id})
        return ok(result)
    except Exception as e:
        logger.error(f"Error polling Hunyuan3D job status: {e}")
        raise ToolError(f"Error polling Hunyuan3D job status: {e}") from e


@mcp.tool()
async def import_generated_asset_hunyuan(
    ctx: Context,
    name: str,
    zip_file_url: str,
) -> dict:
    """Download and import a completed Hunyuan3D asset into the Blender scene.

    Args:
        ctx: MCP request context.
        name: Name to assign to the imported object.
        zip_file_url: Model URL from `ResultFile3Ds`; prefer `.glb`, with `.zip` or `.obj` as fallbacks.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "import_generated_asset_hunyuan",
            {"name": name, "zip_file_url": zip_file_url},
        )
        return ok(result, changed_objects=[name])
    except Exception as e:
        logger.error(f"Error importing Hunyuan3D asset: {e}")
        raise ToolError(f"Error importing Hunyuan3D asset: {e}") from e
