"""Core/meta tools: addon status."""

import logging

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ...addon_manager import EXPECTED_ADDON_PROTOCOL_VERSION
from ..app import mcp
from ..connection import force_addon_handshake, get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
async def get_addon_status(ctx: Context, user_prompt: str = "") -> dict:
    """
    Check whether the connected Blender addon matches this MCP server version.

    If outdated, tells the user how to update via `blender-mcp install-addon`
    (then restart or re-enable the addon in Blender).
    """
    try:
        blender = get_blender_connection()
        result = force_addon_handshake(blender)
        if result is None:
            raise ToolError("Could not determine addon status.")
        payload = {
            "up_to_date": result.up_to_date,
            "protocol_version": result.protocol_version,
            "expected_protocol_version": EXPECTED_ADDON_PROTOCOL_VERSION,
            "addon_version": result.addon_version,
            "capabilities": result.capabilities,
            "blender_version": result.blender_version,
            "source": result.source,
            "warning": result.warning,
            "update_command": "blender-mcp install-addon",
            "after_install": (
                "If the addon file was updated: in Blender, Preferences → Add-ons → "
                "disable/enable 'Interface: Blender MCP', or restart Blender, then Start MCP Server."
            ),
        }
        return ok(payload)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error checking addon status: {e}")
        raise ToolError(f"Error checking addon status: {e}") from e
