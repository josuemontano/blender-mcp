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


def _polyhaven_changed(asset_type: str, result: dict) -> tuple[list[str], list[str]]:
    """
    Split a Polyhaven import result into (changed_objects, changed_resources) by asset type.

    Args:
        asset_type: The asset_type the import was requested with (`hdris`, `textures`, or `models`).
        result: The raw dict returned by the Blender-side Polyhaven import handler.

    Returns:
        tuple[list[str], list[str]]: (changed_objects, changed_resources) - never puts the Polyhaven
        asset ID itself into either list.

    """
    if asset_type == "models":
        return result.get("imported_objects", []), []
    if asset_type == "textures":
        resources = ([result["material"]] if result.get("material") else []) + list(result.get("maps", []))
        return [], resources
    if asset_type == "hdris":
        return [], [result["image_name"]] if result.get("image_name") else []
    return [], []


@mcp.tool()
async def get_polyhaven_categories(ctx: Context, asset_type: AssetType = "hdris") -> dict:
    """
    List Polyhaven categories and asset counts for a selected asset type.

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
    """
    List Polyhaven assets, optionally filtered by one or more categories.

    This catalog query has no free-text parameter; use `categories` to narrow the
    result set and `get_polyhaven_categories` to discover valid categories.

    Args:
        ctx: MCP request context.
        asset_type: One of hdris, textures, models, all.
        categories: Optional comma-separated category names to filter by.

    Returns:
        matching assets with basic metadata and total/returned counts.

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
async def import_polyhaven_asset(
    ctx: Context,
    asset_id: str,
    asset_type: str,
    resolution: str = "1k",
    file_format: str | None = None,
) -> dict:
    """
    Download a Polyhaven HDRI, texture, or model and make it available in Blender.

    The exact result depends on `asset_type`; use `apply_polyhaven_texture` after
    downloading a texture to assign it to a specific object.

    Args:
        ctx: MCP request context.
        asset_id: Polyhaven asset ID from `search_polyhaven_assets`.
        asset_type: Asset type: `hdris`, `textures`, or `models`.
        resolution: Requested resolution, such as `1k`, `2k`, or `4k`.
        file_format: Optional provider-supported format, such as HDR/EXR for HDRIs, JPG/PNG for textures, or
            GLTF/FBX for models.

    Returns:
        a "message" string, plus asset_type-specific fields: for `models`, "imported_objects" (also reported in
        this response's changed_objects); for `textures`, "material" and "maps" (also reported in
        changed_resources); for `hdris`, "image_name" (also reported in changed_resources, and set as the
        active scene world). changed_objects/changed_resources never contain the Polyhaven asset_id itself.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "import_polyhaven_asset",
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
        changed_objects, changed_resources = _polyhaven_changed(asset_type, result)
        return ok(result, changed_objects=changed_objects, changed_resources=changed_resources)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error downloading Polyhaven asset: {e}")
        raise ToolError(f"Error downloading Polyhaven asset: {e}") from e


@mcp.tool()
async def apply_polyhaven_texture(ctx: Context, object_name: str, texture_id: str) -> dict:
    """
    Assign a previously downloaded Polyhaven texture to an object.

    Args:
        ctx: MCP request context.
        object_name: Name of the object to apply the texture to
        texture_id: Polyhaven texture ID; it must have been downloaded first.

    Note: this replaces all of the object's existing material slots with the single new textured material -
    it is not an additive assignment.

    Returns:
        a "message" string, "material" (the new material's name), "maps" (texture map names used), and
        "material_info" (node setup details). "material" and "maps" are also reported in changed_resources.

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
        resources = ([result["material"]] if result.get("material") else []) + list(result.get("maps", []))
        return ok(result, changed_objects=[object_name], changed_resources=resources)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error applying texture: {e}")
        raise ToolError(f"Error applying texture: {e}") from e
