"""Arbitrary Python-code execution inside Blender."""

import logging

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
async def execute_blender_code(ctx: Context, code: str) -> dict:
    """
    Run arbitrary Python in the connected Blender session.

    Use this only when no dedicated Blender MCP tool can perform the task. The code
    can modify the scene, files, and Blender settings; make each call small and
    inspect the result before issuing a dependent call.

    Args:
        ctx: MCP request context.
        code: Python source to run in Blender, typically using the `bpy` API.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return ok(result.get("result", ""))
    except Exception as e:
        logger.error(f"Error executing code: {e}")
        raise ToolError(f"Error executing code: {e}") from e
