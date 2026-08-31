"""The shared FastMCP app instance and server lifespan management."""

import logging

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..addon_manager import check_addon_status_on_startup, format_handshake_log
from .connection import disconnect_blender, get_blender_connection, get_last_handshake

logger = logging.getLogger("BlenderMCPServer")


@asynccontextmanager
async def server_lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """
    Manage server startup and shutdown lifecycle.

    Args:
        _server: FastMCP instance supplied by the SDK.

    Yields:
        AsyncIterator[dict[str, Any]]: Result produced by the operation.

    """
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        logger.info("BlenderMCP server starting up")

        try:
            status = check_addon_status_on_startup()
            if status.needs_action:
                logger.warning(status.message)
            elif status.message:
                logger.info(status.message)
        except Exception as e:
            logger.debug(f"Addon status check skipped: {e}")

        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
            handshake = get_last_handshake()
            if handshake and not handshake.up_to_date:
                logger.warning(format_handshake_log(handshake))
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {e!s}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")

        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        disconnect_blender()
        logger.info("BlenderMCP server shut down")


SERVER_INSTRUCTIONS = """
Every tool below returns one of two shapes:

1. Most tools return a single dict envelope:
   {"ok": bool, "data": ..., "error": None, "warnings": [...], "changed_objects": [...],
    "changed_resources": [...]}
   - Check "ok" before trusting anything else. "ok" is False when the request reached
     Blender but produced no effect - for example an ND tool the user cancelled
     interactively (Esc) - in which case "error" is still None (this is not a transport
     failure) and "warnings" explains why nothing changed.
   - "changed_objects" lists Blender *objects* created/modified/deleted by the call.
     "changed_resources" lists non-object datablocks touched (materials, images, worlds,
     node groups). A name appearing in one of these means it actually changed - not
     merely that it was in the request.
   - "warnings" carries non-fatal notices, most commonly that a topology-changing
     operation invalidated vertex/edge/face indices returned by an earlier
     get_mesh_data call - call get_mesh_data again before reusing indices.
   - A tool-specific failure Blender rejects (bad name, invalid input) raises an MCP
     tool error instead of returning ok:false - stop and fix the input rather than retrying
     the same call.

2. get_viewport_screenshot, get_sketchfab_model_preview, and render_lighting_preview
   return image content followed by the same ok() dict described above. A lighting preview
   returns inline images in CYCLES/EEVEE order for engines without explicit output paths;
   fully explicit output paths return only the envelope. Read the final item for metadata and
   warnings rather than inspecting only the image content.

For any tool exposing limit/offset parameters, pagination metadata is inside the envelope's "data"
dict. Continue with the returned "next_offset" while "truncated" is true. Independent limit/offset
pairs page independent result sets. A validation tool that reports truncation without a continuation
offset must be rerun with a narrower object or collection scope. Catalog tools without limit/offset
return only the provider-bounded result set; do not assume the result is a complete provider catalog.

Before editing, inspect the scene (list_scene_objects, get_object_info, get_mesh_data)
rather than assuming which object is active or selected. Prefer non-destructive tools
(live modifiers, ND) over apply=True/cleanup tools, which are irreversible from this
server's perspective even though Blender's own undo history can still revert them
locally.
""".strip()

# Create the MCP server with lifespan support
mcp = FastMCP("BlenderMCP", lifespan=server_lifespan, instructions=SERVER_INSTRUCTIONS)
# FastMCP 1.x does not expose its low-level server's version in the public
# constructor. Set it explicitly so MCP initialize responses advertise this
# package's version instead of falling back to the unrelated MCP SDK version.
mcp._mcp_server.version = __version__
