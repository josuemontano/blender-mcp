"""Shared types, validation, and Blender transport for texturing tools."""

import logging

from pathlib import Path
from typing import Literal

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

from ...connection import get_blender_connection
from ..envelope import ok

logger = logging.getLogger("BlenderMCPServer")

TargetEngine = Literal["BOTH", "CYCLES", "EEVEE", "BLENDER_EEVEE_NEXT"]


class StrictTextureInput(BaseModel):
    """Reject unknown fields and non-finite numeric input at the MCP boundary."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def explicit_fields(model: BaseModel | None) -> dict:
    """Serialize only fields the caller supplied."""
    return model.model_dump(exclude_none=True, exclude_unset=True) if model else {}


def absolute_path(value: str, label: str) -> str:
    """Require an explicit absolute filesystem path."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ToolError(f"{label} must be an absolute path")
    return str(path)


def call_blender(command: str, params: dict, fallback_objects: list[str] | None = None) -> dict:
    """Dispatch one texturing command and retain Blender's exact change report."""
    try:
        result = get_blender_connection().send_command(command, params)
        changed_objects = result.get("changed_objects", fallback_objects or []) if isinstance(result, dict) else []
        changed_resources = result.get("changed_resources", []) if isinstance(result, dict) else []
        if isinstance(result, dict):
            result = {k: v for k, v in result.items() if k not in {"changed_objects", "changed_resources"}}
        return ok(result, changed_objects=changed_objects, changed_resources=changed_resources)
    except Exception as exc:
        logger.error("Error running %s: %s", command, exc)
        raise ToolError(f"Error running {command}: {exc}") from exc
