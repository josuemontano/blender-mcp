"""Scene/object introspection and viewport screenshot tools."""

import logging
import os
import tempfile

from typing import Literal

from mcp.server.fastmcp import Context, Image
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
async def get_scene_info(ctx: Context, limit: int = 25, offset: int = 0) -> dict:
    """
    Inspect the current Blender scene and page through its objects.

    Args:
        ctx: MCP request context.
        limit: Maximum number of objects to return in this page (default 25, capped at 200).
        offset: Index of the first object to return, for paging through a scene with more objects than fit in one page. The result includes "object_count" (the true total), "returned_count", "truncated", and "next_offset" - when "truncated" is true, call again with offset=next_offset to see the rest of the scene's objects.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info", {"limit": limit, "offset": offset})
        return ok(result)
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {e}")
        raise ToolError(f"Error getting scene info: {e}") from e


@mcp.tool()
async def get_object_info(ctx: Context, object_name: str) -> dict:
    """
    Inspect an object's transform, type, materials, modifiers, and summary geometry data.

    Args:
        ctx: MCP request context.
        object_name: Object name to inspect. For meshes, this returns only vertex/edge/polygon counts; use `get_mesh_data` for element coordinates, normals, indices, or selection state before calling index-based editing tools.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        return ok(result)
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {e}")
        raise ToolError(f"Error getting object info: {e}") from e


@mcp.tool()
async def get_mesh_data(
    ctx: Context,
    object_name: str,
    element_type: Literal["vertices", "edges", "faces", "loops"] = "vertices",
    limit: int = 100,
    offset: int = 0,
    selected_only: bool = False,
) -> dict:
    """
    Paginated inspection of a mesh's topology: vertices, edges, faces, or loops.

    Use this to discover valid indices (with coordinates, normals, and selection state)
    before calling index-based mesh tools such as mesh_extrude, mesh_inset, mesh_bevel,
    mesh_bridge, or mesh_subdivide.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to inspect.
        element_type: One of "vertices", "edges", "faces", "loops". Each element in the result includes its "index" plus type-specific fields: vertices have "co" and "normal"; edges have their two "vertices" indices; faces have their "vertices" indices, "normal", and "material_index"; loops have "vertex_index", "edge_index", and "face_index". Vertices/edges/faces also include a "select" flag.
        limit: Maximum number of elements to return in this page (default 100, capped at 1000).
        offset: Index of the first element to return, for paging through a large mesh.
        selected_only: If True, only return elements currently selected in Edit Mode (not supported for element_type="loops", which has no selection state of its own). The result includes "total" (elements matching the current filter), "total_unfiltered", "returned_count", "truncated", and "next_offset" - when "truncated" is true, call again with offset=next_offset to see the rest.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "get_mesh_data",
            {
                "object_name": object_name,
                "element_type": element_type,
                "limit": limit,
                "offset": offset,
                "selected_only": selected_only,
            },
        )
        return ok(result)
    except Exception as e:
        logger.error(f"Error getting mesh data from Blender: {e}")
        raise ToolError(f"Error getting mesh data: {e}") from e


@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 1000) -> Image:
    """
    Capture the current Blender 3D viewport as an image for visual inspection.

    Args:
        ctx: MCP request context.
        max_size: Maximum pixel length of the image's largest dimension; defaults to 1000.

    Returns:
        the screenshot as an Image.

    Raises:
        Exception: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()

        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")

        result = blender.send_command(
            "get_viewport_screenshot",
            {"max_size": max_size, "filepath": temp_path, "format": "png"},
        )

        if "error" in result:
            raise Exception(result["error"])

        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")

        # Read the file
        with open(temp_path, "rb") as f:
            image_bytes = f.read()

        # Delete the temp file
        os.remove(temp_path)

        return Image(data=image_bytes, format="png")

    except Exception as e:
        logger.error(f"Error capturing screenshot: {e!s}")
        raise Exception(f"Screenshot failed: {e!s}") from e
