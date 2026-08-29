"""ND (HugeMenace) non-destructive hard-surface workflow tools."""

import logging

from typing import Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

BooleanMode = Literal["UNION", "DIFFERENCE", "INTERSECT"]
LodMode = Literal["HIGH", "LOW"]
PulseToggle = Literal["CLEAR_VIEW", "CUSTOM_VIEW", "UTILS"]

_CANCELLED_WARNING = "ND operator was cancelled - the scene may be unchanged"


def _cancelled_warnings(result) -> list[str] | None:
    if isinstance(result, dict) and result.get("cancelled"):
        return [_CANCELLED_WARNING]
    return None


@mcp.tool()
async def nd_boolean(
    ctx: Context,
    object_name: str,
    cutter_object_name: str,
    mode: BooleanMode = "DIFFERENCE",
) -> dict:
    """
    Create a live ND Boolean modifier and retain the cutter as an ND utility object.

    The cutter becomes a wireframe utility object parented to the target instead of
    being deleted, unlike `mesh_boolean`. object_name and cutter_object_name must refer
    to different objects.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object the boolean is applied to (the result/target).
        cutter_object_name: Name of the other mesh object used as the cutter/operand. Must differ from object_name.
        mode: One of UNION, DIFFERENCE, INTERSECT.

    Returns:
        the target and cutter object names and updated vertex/edge/polygon counts.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_boolean",
            {
                "object_name": object_name,
                "cutter_object_name": cutter_object_name,
                "mode": mode,
            },
        )
        return ok(result, changed_objects=[object_name, cutter_object_name], warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error applying ND boolean: {e}")
        raise ToolError(f"Error applying ND boolean: {e}") from e


@mcp.tool()
async def nd_mark_as_util(
    ctx: Context,
    object_names: list[str],
    unmark: bool = False,
    parent_to: str | None = None,
) -> dict:
    """
    Mark objects as ND utilities, or restore previously marked objects.

    Marked objects display as wireframes and are hidden from renders and most viewport
    visibility categories. When parent_to is given (mark path only, unmark=False), each
    marked object is also reparented to it while preserving its world transform,
    replicating the parenting half of ND's real mark_as_util operator. The
    keyboard-modifier-driven behaviors of the real operator (Ctrl-revert,
    Alt-skip-parenting, Shift-recursive-children) have no scriptable equivalent and are
    not replicated here.

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to mark/unmark.
        unmark: If True, restore normal (SOLID/visible) display instead of marking as a utility.
        parent_to: Name of an object to reparent each marked object to, preserving world transform. Only valid when
            unmark is False.

    Returns:
        the affected object names.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_mark_as_util",
            {"object_names": object_names, "unmark": unmark, "parent_to": parent_to},
        )
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error marking ND utility objects: {e}")
        raise ToolError(f"Error marking ND utility objects: {e}") from e


@mcp.tool()
async def nd_clean_utils(ctx: Context, confirm: bool = False) -> dict:
    """
    Remove orphaned boolean/array/mirror/lattice modifiers and their ND utility objects, scene-wide.

    A true dry-run isn't feasible without reimplementing ND's own cleanup logic, so this
    always performs the cleanup - but reports exactly what was removed by diffing the
    scene before and after. Returns "removed_objects" (names of deleted ND utility objects)
    and "removed_modifiers" (each as {"object", "modifier", "type"}).

    Args:
        ctx: MCP request context.
        confirm: Must be True to run - this is scene-wide and destructive with no way to scope or preview it.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_clean_utils", {"confirm": confirm})
        return ok(result, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error cleaning ND utility objects: {e}")
        raise ToolError(f"Error cleaning ND utility objects: {e}") from e


@mcp.tool()
async def nd_create_id_material(
    ctx: Context,
    object_names: list[str],
    material_name: str,
) -> dict:
    """
    Create/assign a single ND ID material to the given mesh/curve objects.

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to assign the material to.
        material_name: Name of the ID material to create/reuse.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_create_id_material",
            {"object_names": object_names, "material_name": material_name},
        )
        return ok(result, changed_objects=object_names, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error creating ND ID material: {e}")
        raise ToolError(f"Error creating ND ID material: {e}") from e


@mcp.tool()
async def nd_bulk_create_id_materials(ctx: Context, object_names: list[str]) -> dict:
    """
    Assign a random distinct ND ID material to each given mesh/curve object.

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to assign distinct ID materials to.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_bulk_create_id_materials", {"object_names": object_names})
        return ok(result, changed_objects=object_names, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error bulk-creating ND ID materials: {e}")
        raise ToolError(f"Error bulk-creating ND ID materials: {e}") from e


@mcp.tool()
async def nd_set_lod_suffix(
    ctx: Context,
    object_names: list[str],
    mode: LodMode = "HIGH",
) -> dict:
    """
    Suffix object (and data-block) names with _high or _low, replacing any existing LOD suffix.

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to rename.
        mode: One of HIGH, LOW.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_set_lod_suffix", {"object_names": object_names, "mode": mode})
        changed = result.get("names", object_names) if isinstance(result, dict) else object_names
        return ok(result, changed_objects=changed, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error setting ND LOD suffix: {e}")
        raise ToolError(f"Error setting ND LOD suffix: {e}") from e


@mcp.tool()
async def nd_single_vertex(
    ctx: Context,
    location: tuple[float, float, float] = (0, 0, 0),
) -> dict:
    """
    Create an ND single-vertex sketch object at location, left in Object mode.

    Args:
        ctx: MCP request context.
        location: [x, y, z] world location for the new vertex object.

    Returns:
        the new object's name and location.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_single_vertex", {"location": list(location)})
        changed = [result.get("name")] if isinstance(result, dict) and result.get("name") else []
        return ok(result, changed_objects=changed, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error creating ND single vertex: {e}")
        raise ToolError(f"Error creating ND single vertex: {e}") from e


@mcp.tool()
async def nd_apply_modifiers(ctx: Context, object_names: list[str]) -> dict:
    """
    Apply eligible modifiers on objects using ND's default REGULAR apply mode.

    This mode is selective and preserves ND's built-in exclusions for bevel,
    weighted normals, and related modifiers. The
    SOFT/HARD/duplicate variants are driven by modifier keys in ND's UI and are not reachable
    from a script.

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to apply modifiers on.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_apply_modifiers", {"object_names": object_names})
        return ok(result, changed_objects=object_names, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error applying ND modifiers: {e}")
        raise ToolError(f"Error applying ND modifiers: {e}") from e


@mcp.tool()
async def nd_pulse_viewport_toggle(ctx: Context, toggle: PulseToggle) -> dict:
    """
    Pulse an ND viewport toggle that has no readable on/off state of its own.

    ND exposes no readable state for CLEAR_VIEW, CUSTOM_VIEW, or UTILS, so this just
    flips ND's internal toggle operator - it is NOT guaranteed idempotent. ND's
    SILHOUETTE toggle is a genuine modal operator and is intentionally not exposed here.
    For the native Blender viewport overlays (cavity, wireframes, face orientation), use
    viewport_overlay_toggle instead - those are true idempotent setters.

    Args:
        ctx: MCP request context.
        toggle: One of CLEAR_VIEW, CUSTOM_VIEW, UTILS.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_pulse_viewport_toggle", {"toggle": toggle})
        return ok(result, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error pulsing ND viewport toggle: {e}")
        raise ToolError(f"Error pulsing ND viewport toggle: {e}") from e


@mcp.tool()
async def nd_capture_utils(ctx: Context) -> dict:
    """
    Display and select all ND utility objects in the scene.

    Args:
        ctx: MCP request context.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_capture_utils", {})
        return ok(result, warnings=_cancelled_warnings(result))
    except Exception as e:
        logger.error(f"Error capturing ND utility objects: {e}")
        raise ToolError(f"Error capturing ND utility objects: {e}") from e
