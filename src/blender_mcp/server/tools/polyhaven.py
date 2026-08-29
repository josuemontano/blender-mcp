"""PolyHaven asset-library integration tools."""

import logging
from typing import Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

AssetType = Literal["hdris", "textures", "models", "all"]


@mcp.tool()
async def get_polyhaven_categories(ctx: Context, asset_type: AssetType = "hdris") -> dict:
    """Get a list of categories for a specific asset type on Polyhaven.

    Args:
        ctx: MCP request context.
        asset_type: One of hdris, textures, models, all.

    Returns:
        the categories and their asset counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        status = blender.send_command("get_polyhaven_status")
        if not status.get("enabled", False):
            raise ToolError(
                "PolyHaven integration is disabled. Select it in the sidebar in BlenderMCP, then run it again."
            )
        result = blender.send_command("get_polyhaven_categories", {"asset_type": asset_type})
        if "error" in result:
            raise ToolError(result["error"])
        return ok({"asset_type": asset_type, "categories": result["categories"]})
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error getting Polyhaven categories: {e}")
        raise ToolError(f"Error getting Polyhaven categories: {e}") from e


@mcp.tool()
async def search_polyhaven_assets(
    ctx: Context,
    asset_type: AssetType = "all",
    categories: str | None = None,
) -> dict:
    """Search for assets on Polyhaven with optional filtering.

    Args:
        ctx: MCP request context.
        asset_type: One of hdris, textures, models, all.
        categories: Optional comma-separated list of categories to filter by.

    Returns:
        matching assets with basic information.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "search_polyhaven_assets",
            {"asset_type": asset_type, "categories": categories},
        )
        if "error" in result:
            raise ToolError(result["error"])
        return ok(
            {
                "total_count": result["total_count"],
                "returned_count": result["returned_count"],
                "assets": result["assets"],
            }
        )
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error searching Polyhaven assets: {e}")
        raise ToolError(f"Error searching Polyhaven assets: {e}") from e


@mcp.tool()
async def download_polyhaven_asset(
    ctx: Context,
    asset_id: str,
    asset_type: str,
    resolution: str = "1k",
    file_format: str | None = None,
) -> dict:
    """Download and import a Polyhaven asset into Blender.

    Args:
        ctx: MCP request context.
        asset_id: The ID of the asset to download
        asset_type: The type of asset (hdris, textures, models)
        resolution: The resolution to download (e.g., 1k, 2k, 4k)
        file_format: Optional file format (e.g., hdr, exr for HDRIs; jpg, png for textures; gltf, fbx for models)

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "download_polyhaven_asset",
            {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "resolution": resolution,
                "file_format": file_format,
            },
        )
        if "error" in result:
            raise ToolError(result["error"])
        if not result.get("success"):
            raise ToolError(f"Failed to download asset: {result.get('message', 'Unknown error')}")
        changed = [asset_id] if asset_type in ("textures", "models") else []
        return ok(result, changed_objects=changed)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error downloading Polyhaven asset: {e}")
        raise ToolError(f"Error downloading Polyhaven asset: {e}") from e


@mcp.tool()
async def apply_polyhaven_texture(ctx: Context, object_name: str, texture_id: str) -> dict:
    """Apply a previously downloaded Polyhaven texture to an object.

    Args:
        ctx: MCP request context.
        object_name: Name of the object to apply the texture to
        texture_id: ID of the Polyhaven texture to apply (must be downloaded first)

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("apply_polyhaven_texture", {"object_name": object_name, "texture_id": texture_id})
        if "error" in result:
            raise ToolError(result["error"])
        if not result.get("success"):
            raise ToolError(f"Failed to apply texture: {result.get('message', 'Unknown error')}")
        return ok(result, changed_objects=[object_name])
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error applying texture: {e}")
        raise ToolError(f"Error applying texture: {e}") from e
