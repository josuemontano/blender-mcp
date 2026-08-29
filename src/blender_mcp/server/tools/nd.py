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
ViewportToggle = Literal["CAVITY", "WIREFRAMES", "FACE_ORIENTATION", "CLEAR_VIEW", "CUSTOM_VIEW", "UTILS"]


@mcp.tool()
async def nd_boolean(
    ctx: Context,
    object_name: str,
    cutter_object_name: str,
    mode: BooleanMode = "DIFFERENCE",
) -> dict:
    """ND non-destructive boolean: live Boolean modifier on object_name, with cutter_object_name

    converted into a wireframe ND utility object parented to it (not deleted, unlike mesh_boolean).

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object the boolean is applied to (the result/target).
        cutter_object_name: Name of the other mesh object used as the cutter/operand.
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
        return ok(result, changed_objects=[object_name, cutter_object_name])
    except Exception as e:
        logger.error(f"Error applying ND boolean: {e}")
        raise ToolError(f"Error applying ND boolean: {e}") from e


@mcp.tool()
async def nd_mark_as_util(
    ctx: Context,
    object_names: list[str],
    unmark: bool = False,
) -> dict:
    """Mark/unmark objects as ND utility objects (wireframe display, hidden from render and most

    viewport visibility categories).

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to mark/unmark.
        unmark: If True, restore normal (SOLID/visible) display instead of marking as a utility.

    Returns:
        the affected object names.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_mark_as_util",
            {"object_names": object_names, "unmark": unmark},
        )
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error marking ND utility objects: {e}")
        raise ToolError(f"Error marking ND utility objects: {e}") from e


@mcp.tool()
async def nd_clean_utils(ctx: Context) -> dict:
    """Remove orphaned boolean/array/mirror/lattice modifiers and their ND utility objects, scene-wide.

    A true dry-run isn't feasible without reimplementing ND's own cleanup logic, so this
    always performs the cleanup - but reports exactly what was removed by diffing the
    scene before and after. Returns "removed_objects" (names of deleted ND utility objects)
    and "removed_modifiers" (each as {"object", "modifier", "type"}).

    Args:
        ctx: MCP request context.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_clean_utils", {})
        return ok(result)
    except Exception as e:
        logger.error(f"Error cleaning ND utility objects: {e}")
        raise ToolError(f"Error cleaning ND utility objects: {e}") from e


@mcp.tool()
async def nd_create_id_material(
    ctx: Context,
    object_names: list[str],
    material_name: str,
) -> dict:
    """Create/assign a single ND ID material to the given mesh/curve objects.

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
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error creating ND ID material: {e}")
        raise ToolError(f"Error creating ND ID material: {e}") from e


@mcp.tool()
async def nd_bulk_create_id_materials(ctx: Context, object_names: list[str]) -> dict:
    """Assign a random distinct ND ID material to each given mesh/curve object.

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
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error bulk-creating ND ID materials: {e}")
        raise ToolError(f"Error bulk-creating ND ID materials: {e}") from e


@mcp.tool()
async def nd_clear_materials(ctx: Context, object_names: list[str]) -> dict:
    """Remove all material slots from the given mesh/curve objects.

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to clear materials from.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_clear_materials", {"object_names": object_names})
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error clearing ND materials: {e}")
        raise ToolError(f"Error clearing ND materials: {e}") from e


@mcp.tool()
async def nd_set_lod_suffix(
    ctx: Context,
    object_names: list[str],
    mode: LodMode = "HIGH",
) -> dict:
    """Suffix object (and data-block) names with _high or _low, replacing any existing LOD suffix.

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
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error setting ND LOD suffix: {e}")
        raise ToolError(f"Error setting ND LOD suffix: {e}") from e


@mcp.tool()
async def nd_name_sync(ctx: Context, object_names: list[str]) -> dict:
    """Sync each object's data-block name to match its object name.

    Args:
        ctx: MCP request context.
        object_names: Names of the objects to sync.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_name_sync", {"object_names": object_names})
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error syncing ND names: {e}")
        raise ToolError(f"Error syncing ND names: {e}") from e


@mcp.tool()
async def nd_single_vertex(
    ctx: Context,
    location: tuple[float, float, float] = (0, 0, 0),
) -> dict:
    """Create an ND single-vertex sketch object at location, left in Object mode.

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
        return ok(result, changed_objects=changed)
    except Exception as e:
        logger.error(f"Error creating ND single vertex: {e}")
        raise ToolError(f"Error creating ND single vertex: {e}") from e


@mcp.tool()
async def nd_clear_edge_marks(ctx: Context, object_name: str) -> dict:
    """Remove sharp/seam/freestyle edge marks from a mesh object.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to clear edge marks from.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_clear_edge_marks", {"object_name": object_name})
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error clearing ND edge marks: {e}")
        raise ToolError(f"Error clearing ND edge marks: {e}") from e


@mcp.tool()
async def nd_clear_vertex_groups(ctx: Context, object_name: str) -> dict:
    """Remove all vertex groups from a mesh object.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to clear vertex groups from.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_clear_vertex_groups", {"object_name": object_name})
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error clearing ND vertex groups: {e}")
        raise ToolError(f"Error clearing ND vertex groups: {e}") from e


@mcp.tool()
async def nd_apply_modifiers(ctx: Context, object_names: list[str]) -> dict:
    """Apply modifiers on the given objects via ND. Always runs ND's default REGULAR apply mode

    (selective, with ND's built-in exclusions for bevel/weighted-normals/etc.) - the
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
        return ok(result, changed_objects=object_names)
    except Exception as e:
        logger.error(f"Error applying ND modifiers: {e}")
        raise ToolError(f"Error applying ND modifiers: {e}") from e


@mcp.tool()
async def nd_viewport_toggle(ctx: Context, toggle: ViewportToggle, enabled: bool) -> dict:
    """Set an ND-related viewport display toggle to an explicit on/off state.

    For CAVITY, WIREFRAMES, and FACE_ORIENTATION this is a true idempotent setter backed
    by Blender's own viewport overlay properties - calling it again with the same `enabled`
    value is a no-op. For CLEAR_VIEW, CUSTOM_VIEW, and UTILS, ND exposes no readable on/off
    state, so `enabled` is ignored and the call just flips ND's internal toggle operator -
    it is NOT guaranteed idempotent for those three.

    Args:
        ctx: MCP request context.
        toggle: One of CAVITY, WIREFRAMES, FACE_ORIENTATION, CLEAR_VIEW, CUSTOM_VIEW, UTILS. (ND's SILHOUETTE toggle is a genuine modal operator and is intentionally not exposed here.)
        enabled: Desired on/off state. Ignored for CLEAR_VIEW, CUSTOM_VIEW, and UTILS.

    Returns:
        dict: Result produced by the operation.

    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_viewport_toggle", {"toggle": toggle, "enabled": enabled})
        return ok(result)
    except Exception as e:
        logger.error(f"Error toggling ND viewport setting: {e}")
        raise ToolError(f"Error toggling ND viewport setting: {e}") from e


@mcp.tool()
async def nd_capture_utils(ctx: Context) -> dict:
    """Display and select all ND utility objects in the scene.

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
        return ok(result)
    except Exception as e:
        logger.error(f"Error capturing ND utility objects: {e}")
        raise ToolError(f"Error capturing ND utility objects: {e}") from e
