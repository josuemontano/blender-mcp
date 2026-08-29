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
    """Search for models on Sketchfab with optional filtering.

    Args:
        ctx: MCP request context.
        query: Text to search for
        categories: Optional comma-separated list of categories
        count: Maximum number of results to return (default 20)
        downloadable: Whether to include only downloadable models (default True)

    Returns:
        the matching models.
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
    """Get a preview thumbnail of a Sketchfab model by its UID.

    Use this to visually confirm a model before downloading.

    Args:
        ctx: MCP request context.
        uid: The unique identifier of the Sketchfab model (obtained from search_sketchfab_models)

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
        logger.error(f"Error getting Sketchfab preview: {str(e)}")
        raise Exception(f"Failed to get preview: {str(e)}") from e


@mcp.tool()
async def download_sketchfab_model(ctx: Context, uid: str, target_size: float) -> dict:
    """Download and import a Sketchfab model by its UID.

    The model will be scaled so its largest dimension equals target_size.

    Args:
        ctx: MCP request context.
        uid: The unique identifier of the Sketchfab model
        target_size: REQUIRED. The target size in Blender units/meters for the largest dimension. You must specify the desired size for the model. Examples: - Chair: target_size=1.0 (1 meter tall) - Table: target_size=0.75 (75cm tall) - Car: target_size=4.5 (4.5 meters long) - Person: target_size=1.7 (1.7 meters tall) - Small object (cup, phone): target_size=0.1 to 0.3

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
