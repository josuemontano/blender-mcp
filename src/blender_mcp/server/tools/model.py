"""Higher-level modeling tools built on top of mesh modifiers/operations."""

import logging
from typing import Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

PrimitiveType = Literal["CUBE", "SPHERE", "CYLINDER", "CONE", "TORUS", "PLANE", "CURVE"]
SymmetrizeDirection = Literal[
    "NEGATIVE_X",
    "POSITIVE_X",
    "NEGATIVE_Y",
    "POSITIVE_Y",
    "NEGATIVE_Z",
    "POSITIVE_Z",
]
Axis = Literal["X", "Y", "Z"]


@mcp.tool()
async def model_match_reference(
    ctx: Context,
    object_name: str,
    reference_object_name: str,
    match_location: bool = True,
    match_rotation: bool = True,
    match_scale: bool = True,
    user_prompt: str = "",
) -> dict:
    """
    Align an object's transform to another object's transform in the scene.

    Parameters:
    - object_name: Name of the object to move/rotate/scale.
    - reference_object_name: Name of the object whose transform to copy.
    - match_location: Copy the reference object's location.
    - match_rotation: Copy the reference object's rotation.
    - match_scale: Copy the reference object's scale.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the object's name and resulting location/rotation/scale.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_match_reference",
            {
                "object_name": object_name,
                "reference_object_name": reference_object_name,
                "match_location": match_location,
                "match_rotation": match_rotation,
                "match_scale": match_scale,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error matching reference transform: {e}")
        raise ToolError(f"Error matching reference transform: {e}") from e


@mcp.tool()
async def model_blockout(
    ctx: Context,
    name: str,
    primitive_type: PrimitiveType = "CUBE",
    size: tuple[float, float, float] = (1, 1, 1),
    location: tuple[float, float, float] = (0, 0, 0),
    user_prompt: str = "",
) -> dict:
    """
    Create a simple placeholder primitive scaled to size, tagged as a blockout proxy for later refinement.

    Parameters:
    - name: Name for the created blockout object.
    - primitive_type: One of CUBE, SPHERE, CYLINDER, CONE, TORUS, PLANE, CURVE.
    - size: [x, y, z] scale applied to the primitive.
    - location: [x, y, z] location for the new object.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the created object's name, type, location, and scale.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_blockout",
            {
                "name": name,
                "primitive_type": primitive_type,
                "size": list(size),
                "location": list(location),
            },
        )
        return ok(result, changed_objects=[name])
    except Exception as e:
        logger.error(f"Error creating blockout: {e}")
        raise ToolError(f"Error creating blockout: {e}") from e


@mcp.tool()
async def model_refine(
    ctx: Context,
    object_name: str,
    levels: int = 1,
    apply: bool = False,
    user_prompt: str = "",
) -> dict:
    """
    Smooth a mesh and increase its effective resolution via a Subdivision Surface modifier.

    Parameters:
    - object_name: Name of the mesh object to refine.
    - levels: Subdivision levels (viewport and render).
    - apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the object's name, whether the modifier was applied, and updated vertex/edge/polygon counts.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_refine",
            {
                "object_name": object_name,
                "levels": levels,
                "apply": apply,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error refining model: {e}")
        raise ToolError(f"Error refining model: {e}") from e


@mcp.tool()
async def model_detail(
    ctx: Context,
    object_name: str,
    strength: float = 0.1,
    scale: float = 5.0,
    texture_type: str = "NOISE",
    apply: bool = False,
    user_prompt: str = "",
) -> dict:
    """
    Add fine procedural surface detail to a mesh via a Displace modifier driven by a procedural texture.

    Parameters:
    - object_name: Name of the mesh object to detail.
    - strength: Displacement strength.
    - scale: Noise scale of the driving texture.
    - texture_type: Blender texture type to drive the displacement, e.g. NOISE or VORONOI.
    - apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the object's name, whether the modifier was applied, and updated vertex/edge/polygon counts.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_detail",
            {
                "object_name": object_name,
                "strength": strength,
                "scale": scale,
                "texture_type": texture_type,
                "apply": apply,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error adding model detail: {e}")
        raise ToolError(f"Error adding model detail: {e}") from e


@mcp.tool()
async def model_symmetrize(
    ctx: Context,
    object_name: str,
    direction: SymmetrizeDirection = "NEGATIVE_X",
    user_prompt: str = "",
) -> dict:
    """
    Symmetrize a mesh across an axis, mirroring one half of the geometry onto the other.

    Parameters:
    - object_name: Name of the mesh object to symmetrize.
    - direction: Side overwritten by its mirror. One of NEGATIVE_X, POSITIVE_X, NEGATIVE_Y, POSITIVE_Y, NEGATIVE_Z, POSITIVE_Z.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the object's name and updated vertex/edge/polygon counts.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_symmetrize",
            {
                "object_name": object_name,
                "direction": direction,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error symmetrizing model: {e}")
        raise ToolError(f"Error symmetrizing model: {e}") from e


@mcp.tool()
async def model_mirror(
    ctx: Context,
    object_name: str,
    axis: Axis = "X",
    merge: bool = True,
    clip: bool = True,
    apply: bool = False,
    user_prompt: str = "",
) -> dict:
    """
    Add a Mirror modifier to an object across the given axis.

    Parameters:
    - object_name: Name of the mesh object to mirror.
    - axis: One of X, Y, Z.
    - merge: Weld coincident vertices at the mirror seam.
    - clip: Prevent vertices from crossing the mirror plane during transforms. Independent of merge.
    - apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the object's name, whether the modifier was applied, and updated vertex/edge/polygon counts.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_mirror",
            {
                "object_name": object_name,
                "axis": axis,
                "merge": merge,
                "clip": clip,
                "apply": apply,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error mirroring model: {e}")
        raise ToolError(f"Error mirroring model: {e}") from e


@mcp.tool()
async def model_array(
    ctx: Context,
    object_name: str,
    count: int = 2,
    relative_offset: tuple[float, float, float] = (1, 0, 0),
    apply: bool = False,
    user_prompt: str = "",
) -> dict:
    """
    Add a linear Array modifier to an object, duplicating it along an offset direction.

    Parameters:
    - object_name: Name of the mesh object to array.
    - count: Number of copies (including the original).
    - relative_offset: [x, y, z] offset between copies, relative to the object's bounding box.
    - apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the object's name, whether the modifier was applied, and updated vertex/edge/polygon counts.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_array",
            {
                "object_name": object_name,
                "count": count,
                "relative_offset": list(relative_offset),
                "apply": apply,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error arraying model: {e}")
        raise ToolError(f"Error arraying model: {e}") from e


@mcp.tool()
async def model_radial_array(
    ctx: Context,
    object_name: str,
    count: int = 6,
    axis: Axis = "Z",
    apply: bool = False,
    user_prompt: str = "",
) -> dict:
    """
    Duplicate an object radially around its origin, evenly spaced about an axis.

    Parameters:
    - object_name: Name of the mesh object to array.
    - count: Number of copies around the circle (including the original). Must be at least 2.
    - axis: One of X, Y, Z — the axis to rotate around.
    - apply: If True, bake the modifier into the mesh and remove the helper empty. If False (default), leave both live.
    - user_prompt: The user's own words describing what they want, quoted verbatim (do not paraphrase or summarise). Pass the same goal on every call in a multi-step task so each action is linked to the intent behind it. Never substitute your own sub-goal, plan step, or status text; if the user has given no new instruction, repeat their previous words unchanged.

    Returns the object's name, whether the modifier was applied, and updated vertex/edge/polygon counts.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "model_radial_array",
            {
                "object_name": object_name,
                "count": count,
                "axis": axis,
                "apply": apply,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error creating radial array: {e}")
        raise ToolError(f"Error creating radial array: {e}") from e
