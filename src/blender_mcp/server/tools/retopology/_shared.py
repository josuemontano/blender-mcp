"""Shared plumbing and types for the retopology tool package."""

import logging

from typing import Any, Literal

from mcp.server.fastmcp.exceptions import ToolError

from ...connection import get_blender_connection

logger = logging.getLogger("BlenderMCPServer")

RetopologyProfile = Literal["CHARACTER", "HARD_SURFACE", "VFX", "GAME"]


def _call(command: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return get_blender_connection().send_command(command, params)
    except Exception as exc:
        logger.error("%s failed: %s", command, exc)
        raise ToolError(f"{command} failed: {exc}") from exc
