"""The shared FastMCP app instance and server lifespan management."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..addon_manager import check_addon_status_on_startup, format_handshake_log
from .connection import disconnect_blender, get_blender_connection, get_last_handshake

logger = logging.getLogger("BlenderMCPServer")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server startup and shutdown lifecycle

    Args:
        server: Value for server.

    Returns:
        AsyncIterator[dict[str, Any]]: Result produced by the operation.
    """
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        # Just log that we're starting up
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
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")

        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        disconnect_blender()
        logger.info("BlenderMCP server shut down")


# Create the MCP server with lifespan support
mcp = FastMCP("BlenderMCP", lifespan=server_lifespan)
