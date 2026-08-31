# ruff: file-ignore[docstring-missing-returns, multi-line-summary-second-line, unused-function-argument]
"""Typed tools for removing fluid modifier components."""

import asyncio

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field

from ...app import mcp
from .inspection_and_setup import _call


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FluidComponentTarget(_StrictModel):
    object_name: str
    modifier_name: str
    remove_owned_helper_object: bool = False


@mcp.tool()
async def remove_fluid_components(
    ctx: Context,
    targets: Annotated[list[FluidComponentTarget], Field(min_length=1)],
    accept_orphaned_cache: bool = False,
) -> dict:
    """Remove exact fluid modifiers and optionally MCP-owned helper objects after a cache-orphan preflight.

    Preflight rejects removal if it would orphan an existing on-disk bake, unless accept_orphaned_cache=True.
    """
    return await asyncio.to_thread(
        _call,
        "remove_fluid_components",
        {
            "targets": [item.model_dump() for item in targets],
            "accept_orphaned_cache": accept_orphaned_cache,
        },
        [item.object_name for item in targets],
    )
