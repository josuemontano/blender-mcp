"""Core/meta tools: addon status and integration status."""

import logging

from typing import Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ...addon_manager import EXPECTED_ADDON_PROTOCOL_VERSION
from ..app import mcp
from ..connection import force_addon_handshake, get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

Provider = Literal["polyhaven", "hyper3d", "sketchfab", "hunyuan3d", "nd"]

_STATUS_COMMANDS: dict[Provider, str] = {
    "polyhaven": "get_polyhaven_status",
    "hyper3d": "get_hyper3d_status",
    "sketchfab": "get_sketchfab_status",
    "hunyuan3d": "get_hunyuan3d_status",
    "nd": "get_nd_status",
}


@mcp.tool()
async def get_integration_status(ctx: Context, provider: Provider | None = None) -> dict:
    """
    Check whether an optional third-party integration is enabled in Blender.

    Args:
        ctx: MCP request context.
        provider: One of "polyhaven", "hyper3d", "sketchfab", "hunyuan3d", "nd". If omitted, checks all five and
            returns a dict keyed by provider name. Each provider's result is {"enabled": bool, "message": str}
            describing whether that integration's features are available and, if not, how to enable it.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        if provider is not None:
            result = blender.send_command(_STATUS_COMMANDS[provider])
            return ok(result)
        results = {name: blender.send_command(command) for name, command in _STATUS_COMMANDS.items()}
        return ok(results)
    except Exception as e:
        logger.error(f"Error checking integration status: {e}")
        raise ToolError(f"Error checking integration status: {e}") from e


@mcp.tool()
async def get_addon_status(ctx: Context) -> dict:
    """
    Check whether the connected Blender addon matches this MCP server version.

    If outdated, tells the user how to update via `blender-mcp install-addon`
    (then restart or re-enable the addon in Blender).

    Args:
        ctx: MCP request context.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

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
