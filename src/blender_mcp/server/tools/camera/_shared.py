"""Shared plumbing and cross-file types for the camera tool package."""

import logging

from typing import Literal

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

from ...connection import get_blender_connection
from .._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

TrackAxis = Literal[
    "TRACK_X",
    "TRACK_Y",
    "TRACK_Z",
    "TRACK_NEGATIVE_X",
    "TRACK_NEGATIVE_Y",
    "TRACK_NEGATIVE_Z",
]
UpAxis = Literal["UP_X", "UP_Y", "UP_Z"]
LockAxis = Literal["LOCK_X", "LOCK_Y", "LOCK_Z"]
ConstraintSpace = Literal["WORLD", "CUSTOM", "POSE", "LOCAL_WITH_PARENT", "LOCAL"]
FollowForwardAxis = Literal[
    "FORWARD_X",
    "FORWARD_Y",
    "FORWARD_Z",
    "TRACK_NEGATIVE_X",
    "TRACK_NEGATIVE_Y",
    "TRACK_NEGATIVE_Z",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _dump(model: BaseModel | None) -> dict | None:
    return model.model_dump(exclude_none=True, exclude_unset=True) if model is not None else None


def _tool_params(values: dict) -> dict:
    """Remove FastMCP's context-only argument from a local tool payload."""
    return {key: value for key, value in values.items() if key != "ctx"}


def _call(command: str, params: dict, changed_objects: list[str] | None = None) -> dict:
    try:
        result = get_blender_connection().send_command(command, params)
        changed = result.get("changed_objects", changed_objects or []) if isinstance(result, dict) else changed_objects
        resources = result.get("changed_resources", []) if isinstance(result, dict) else []
        if isinstance(result, dict):
            result = {
                key: value for key, value in result.items() if key not in {"changed_objects", "changed_resources"}
            }
        return ok(result, changed_objects=changed or [], changed_resources=resources)
    except Exception as exc:
        logger.error("Error running %s: %s", command, exc)
        raise ToolError(f"Error running {command}: {exc}") from exc
