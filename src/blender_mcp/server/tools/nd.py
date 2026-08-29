"""ND (HugeMenace) non-destructive hard-surface workflow tools."""

import logging

from mcp.server.fastmcp import Context

from ..app import mcp
from ..connection import get_blender_connection

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
async def nd_boolean(
    ctx: Context,
    object_name: str,
    cutter_object_name: str,
    mode: str = "DIFFERENCE",
    user_prompt: str = "",
) -> str:
    """
    ND non-destructive boolean: live Boolean modifier on object_name, with cutter_object_name
    converted into a wireframe ND utility object parented to it (not deleted, unlike mesh_boolean).

    Parameters:
    - object_name: Name of the mesh object the boolean is applied to (the result/target).
    - cutter_object_name: Name of the other mesh object used as the cutter/operand.
    - mode: One of UNION, DIFFERENCE, INTERSECT.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the target and cutter object names and updated vertex/edge/polygon counts.
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
        return result
    except Exception as e:
        logger.error(f"Error applying ND boolean: {str(e)}")
        return f"Error applying ND boolean: {str(e)}"


@mcp.tool()
async def nd_mark_as_util(
    ctx: Context,
    object_names: list[str],
    unmark: bool = False,
    user_prompt: str = "",
) -> str:
    """
    Mark/unmark objects as ND utility objects (wireframe display, hidden from render and most
    viewport visibility categories).

    Parameters:
    - object_names: Names of the objects to mark/unmark.
    - unmark: If True, restore normal (SOLID/visible) display instead of marking as a utility.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the affected object names.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_mark_as_util",
            {"object_names": object_names, "unmark": unmark},
        )
        return result
    except Exception as e:
        logger.error(f"Error marking ND utility objects: {str(e)}")
        return f"Error marking ND utility objects: {str(e)}"


@mcp.tool()
async def nd_clean_utils(ctx: Context, user_prompt: str = "") -> str:
    """
    Remove orphaned boolean/array/mirror/lattice modifiers and their ND utility objects, scene-wide.

    Parameters:
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_clean_utils", {})
        return result
    except Exception as e:
        logger.error(f"Error cleaning ND utility objects: {str(e)}")
        return f"Error cleaning ND utility objects: {str(e)}"


@mcp.tool()
async def nd_create_id_material(
    ctx: Context,
    object_names: list[str],
    material_name: str,
    user_prompt: str = "",
) -> str:
    """
    Create/assign a single ND ID material to the given mesh/curve objects.

    Parameters:
    - object_names: Names of the objects to assign the material to.
    - material_name: Name of the ID material to create/reuse.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_create_id_material",
            {"object_names": object_names, "material_name": material_name},
        )
        return result
    except Exception as e:
        logger.error(f"Error creating ND ID material: {str(e)}")
        return f"Error creating ND ID material: {str(e)}"


@mcp.tool()
async def nd_bulk_create_id_materials(
    ctx: Context, object_names: list[str], user_prompt: str = ""
) -> str:
    """
    Assign a random distinct ND ID material to each given mesh/curve object.

    Parameters:
    - object_names: Names of the objects to assign distinct ID materials to.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_bulk_create_id_materials", {"object_names": object_names}
        )
        return result
    except Exception as e:
        logger.error(f"Error bulk-creating ND ID materials: {str(e)}")
        return f"Error bulk-creating ND ID materials: {str(e)}"


@mcp.tool()
async def nd_clear_materials(
    ctx: Context, object_names: list[str], user_prompt: str = ""
) -> str:
    """
    Remove all material slots from the given mesh/curve objects.

    Parameters:
    - object_names: Names of the objects to clear materials from.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_clear_materials", {"object_names": object_names}
        )
        return result
    except Exception as e:
        logger.error(f"Error clearing ND materials: {str(e)}")
        return f"Error clearing ND materials: {str(e)}"


@mcp.tool()
async def nd_set_lod_suffix(
    ctx: Context,
    object_names: list[str],
    mode: str = "HIGH",
    user_prompt: str = "",
) -> str:
    """
    Suffix object (and data-block) names with _high or _low, replacing any existing LOD suffix.

    Parameters:
    - object_names: Names of the objects to rename.
    - mode: One of HIGH, LOW.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_set_lod_suffix", {"object_names": object_names, "mode": mode}
        )
        return result
    except Exception as e:
        logger.error(f"Error setting ND LOD suffix: {str(e)}")
        return f"Error setting ND LOD suffix: {str(e)}"


@mcp.tool()
async def nd_name_sync(
    ctx: Context, object_names: list[str], user_prompt: str = ""
) -> str:
    """
    Sync each object's data-block name to match its object name.

    Parameters:
    - object_names: Names of the objects to sync.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_name_sync", {"object_names": object_names})
        return result
    except Exception as e:
        logger.error(f"Error syncing ND names: {str(e)}")
        return f"Error syncing ND names: {str(e)}"


@mcp.tool()
async def nd_single_vertex(
    ctx: Context,
    location: list[float] = (0, 0, 0),
    user_prompt: str = "",
) -> str:
    """
    Create an ND single-vertex sketch object at location, left in Object mode.

    Parameters:
    - location: [x, y, z] world location for the new vertex object.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the new object's name and location.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_single_vertex", {"location": list(location)})
        return result
    except Exception as e:
        logger.error(f"Error creating ND single vertex: {str(e)}")
        return f"Error creating ND single vertex: {str(e)}"


@mcp.tool()
async def nd_clear_edge_marks(
    ctx: Context, object_name: str, user_prompt: str = ""
) -> str:
    """
    Remove sharp/seam/freestyle edge marks from a mesh object.

    Parameters:
    - object_name: Name of the mesh object to clear edge marks from.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_clear_edge_marks", {"object_name": object_name}
        )
        return result
    except Exception as e:
        logger.error(f"Error clearing ND edge marks: {str(e)}")
        return f"Error clearing ND edge marks: {str(e)}"


@mcp.tool()
async def nd_clear_vertex_groups(
    ctx: Context, object_name: str, user_prompt: str = ""
) -> str:
    """
    Remove all vertex groups from a mesh object.

    Parameters:
    - object_name: Name of the mesh object to clear vertex groups from.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_clear_vertex_groups", {"object_name": object_name}
        )
        return result
    except Exception as e:
        logger.error(f"Error clearing ND vertex groups: {str(e)}")
        return f"Error clearing ND vertex groups: {str(e)}"


@mcp.tool()
async def nd_apply_modifiers(
    ctx: Context, object_names: list[str], user_prompt: str = ""
) -> str:
    """
    Apply modifiers on the given objects via ND. Always runs ND's default REGULAR apply mode
    (selective, with ND's built-in exclusions for bevel/weighted-normals/etc.) - the
    SOFT/HARD/duplicate variants are driven by modifier keys in ND's UI and are not reachable
    from a script.

    Parameters:
    - object_names: Names of the objects to apply modifiers on.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "nd_apply_modifiers", {"object_names": object_names}
        )
        return result
    except Exception as e:
        logger.error(f"Error applying ND modifiers: {str(e)}")
        return f"Error applying ND modifiers: {str(e)}"


@mcp.tool()
async def nd_viewport_toggle(ctx: Context, toggle: str, user_prompt: str = "") -> str:
    """
    Toggle an ND viewport display setting.

    Parameters:
    - toggle: One of CAVITY, WIREFRAMES, FACE_ORIENTATION, CLEAR_VIEW, CUSTOM_VIEW, UTILS.
      (ND's SILHOUETTE toggle is a genuine modal operator and is intentionally not exposed here.)
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_viewport_toggle", {"toggle": toggle})
        return result
    except Exception as e:
        logger.error(f"Error toggling ND viewport setting: {str(e)}")
        return f"Error toggling ND viewport setting: {str(e)}"


@mcp.tool()
async def nd_capture_utils(ctx: Context, user_prompt: str = "") -> str:
    """
    Display and select all ND utility objects in the scene.

    Parameters:
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("nd_capture_utils", {})
        return result
    except Exception as e:
        logger.error(f"Error capturing ND utility objects: {str(e)}")
        return f"Error capturing ND utility objects: {str(e)}"


@mcp.tool()
async def get_nd_status(ctx: Context, user_prompt: str = "") -> str:
    """
    Check if ND (HugeMenace) non-destructive workflow integration is enabled in Blender.
    Returns a message indicating whether ND features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_nd_status")
        return result.get("message", "")
    except Exception as e:
        logger.error(f"Error checking ND status: {str(e)}")
        return f"Error checking ND status: {str(e)}"
