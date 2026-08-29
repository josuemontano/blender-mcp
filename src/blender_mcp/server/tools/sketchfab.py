"""Sketchfab asset-library integration tools."""

import base64
import logging

from mcp.server.fastmcp import Context, Image
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
async def search_sketchfab_models(
    ctx: Context,
    query: str,
    categories: str | None = None,
    count: int = 20,
    downloadable: bool = True,
) -> dict:
    """
    Search the Sketchfab catalog for models, optionally filtering by category and downloadability.

    Args:
        ctx: MCP request context.
        query: Search terms describing the desired model.
        categories: Optional comma-separated Sketchfab categories.
        count: Maximum number of results to return; defaults to 20.
        downloadable: When true (default), return only models that can be downloaded.

    Returns:
        matching model metadata, including UIDs for previewing or importing.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        logger.info(
            f"Searching Sketchfab models with query: {query}, categories: {categories}, count: {count}, downloadable: {downloadable}"
        )
        result = blender.send_command(
            "search_sketchfab_models",
            {
                "query": query,
                "categories": categories,
                "count": count,
                "downloadable": downloadable,
            },
        )
        if result is None:
            raise ToolError("Received no response from Sketchfab search")
        if "error" in result:
            raise ToolError(result["error"])
        models = result.get("results", []) or []
        return ok({"query": query, "count": len(models), "results": models})
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error searching Sketchfab models: {e}")
        raise ToolError(f"Error searching Sketchfab models: {e}") from e


@mcp.tool()
async def get_sketchfab_model_preview(ctx: Context, uid: str) -> Image:
    """
    Return a Sketchfab model's thumbnail for visual review before import.

    Use this to visually confirm a model before downloading.

    Args:
        ctx: MCP request context.
        uid: Model UID returned by `search_sketchfab_models`.

    Returns:
        the model's thumbnail as an Image for visual confirmation.

    Raises:
        Exception: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        logger.info(f"Getting Sketchfab model preview for UID: {uid}")

        result = blender.send_command("get_sketchfab_model_preview", {"uid": uid})

        if result is None:
            raise Exception("Received no response from Blender")

        if "error" in result:
            raise Exception(result["error"])

        # Decode base64 image data
        image_data = base64.b64decode(result["image_data"])
        img_format = result.get("format", "jpeg")

        # Log model info
        model_name = result.get("model_name", "Unknown")
        author = result.get("author", "Unknown")
        logger.info(f"Preview retrieved for '{model_name}' by {author}")

        return Image(data=image_data, format=img_format)

    except Exception as e:
        logger.error(f"Error getting Sketchfab preview: {e!s}")
        raise Exception(f"Failed to get preview: {e!s}") from e


@mcp.tool()
async def download_sketchfab_model(ctx: Context, uid: str, target_size: float) -> dict:
    """
    Download and import a Sketchfab model, scaling its largest dimension to a chosen size.

    The model will be scaled so its largest dimension equals target_size.

    Args:
        ctx: MCP request context.
        uid: Downloadable model UID returned by `search_sketchfab_models`.
        target_size: Required target size in Blender units for the imported model's largest dimension; for example, 1.0 for a chair or 4.5 for a car.

    Returns:
        import details including object names, dimensions, and bounding box. The model must be downloadable and you must have proper access rights.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        logger.info(f"Downloading Sketchfab model: {uid}, target_size={target_size}")

        result = blender.send_command(
            "download_sketchfab_model",
            {
                "uid": uid,
                "normalize_size": True,  # Always normalize
                "target_size": target_size,
            },
        )

        if result is None:
            raise ToolError("Received no response from Sketchfab download request")
        if "error" in result:
            raise ToolError(result["error"])
        if not result.get("success"):
            raise ToolError(f"Failed to download model: {result.get('message', 'Unknown error')}")
        return ok(result, changed_objects=result.get("imported_objects", []))
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error downloading Sketchfab model: {e}")
        raise ToolError(f"Error downloading Sketchfab model: {e}") from e
