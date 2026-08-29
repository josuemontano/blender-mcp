"""Higher-level modeling tools built on top of mesh modifiers/operations."""

import logging
from typing import Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

Axis = Literal["X", "Y", "Z"]
Space = Literal["LOCAL", "WORLD"]


@mcp.tool()
async def model_match_reference(
    ctx: Context,
    object_name: str,
    reference_object_name: str,
    match_location: bool = True,
    match_rotation: bool = True,
    match_scale: bool = True,
    space: Space = "WORLD",
) -> dict:
    """
    Align an object's transform to another object's transform in the scene.

    Parameters:
    - object_name: Name of the object to move/rotate/scale.
    - reference_object_name: Name of the object whose transform to copy.
    - match_location: Copy the reference object's location.
    - match_rotation: Copy the reference object's rotation.
    - match_scale: Copy the reference object's scale.
    - space: "WORLD" (default) matches visually even across differently-parented
      objects, and correctly handles quaternion/axis-angle rotation modes, by
      decomposing/recomposing world matrices. "LOCAL" copies the raw local
      location/rotation/scale properties instead.

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
                "space": space,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error matching reference transform: {e}")
        raise ToolError(f"Error matching reference transform: {e}") from e


@mcp.tool()
async def model_refine(
    ctx: Context,
    object_name: str,
    levels: int = 1,
    apply: bool = False,
) -> dict:
    """
    Smooth a mesh and increase its effective resolution via a Subdivision Surface modifier.

    Parameters:
    - object_name: Name of the mesh object to refine.
    - levels: Subdivision levels (viewport and render).
    - apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.

    Returns the object's name, whether the modifier was applied, base vertex/edge/polygon
    counts, and (when apply=False) an "evaluated" count, "modifier" name, and world-space
    "bounds" reflecting the live modifier's effect.
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
async def add_procedural_displacement(
    ctx: Context,
    object_name: str,
    strength: float = 0.1,
    scale: float = 5.0,
    texture_type: str = "NOISE",
    apply: bool = False,
    subdivide: bool = False,
) -> dict:
    """
    Add fine procedural surface detail to a mesh via a Displace modifier driven by a procedural texture.

    Displace only offsets existing vertices - it cannot produce fine detail on a mesh
    that doesn't already have enough topology. Set subdivide=True to add a Subdivision
    Surface pass first, or ensure the mesh is already dense enough.

    Parameters:
    - object_name: Name of the mesh object to detail.
    - strength: Displacement strength.
    - scale: Noise scale of the driving texture.
    - texture_type: Blender texture type to drive the displacement, e.g. NOISE or VORONOI.
    - apply: If True, bake the modifier(s) into the mesh and remove the generated texture
      datablock. If False (default), leave everything as live modifiers/texture.
    - subdivide: If True, add a Subdivision Surface modifier before adding the Displace
      modifier, to ensure enough topology exists for visible detail. It is only baked in
      when apply is also True - with apply=False both modifiers stay live.

    Returns the object's name, whether the modifier was applied, base vertex/edge/polygon
    counts, and (when apply=False) an "evaluated" count, "modifier" name, and world-space
    "bounds" reflecting the live modifier's effect.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "add_procedural_displacement",
            {
                "object_name": object_name,
                "strength": strength,
                "scale": scale,
                "texture_type": texture_type,
                "apply": apply,
                "subdivide": subdivide,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error adding procedural displacement: {e}")
        raise ToolError(f"Error adding procedural displacement: {e}") from e


@mcp.tool()
async def model_mirror(
    ctx: Context,
    object_name: str,
    axis: Axis = "X",
    merge: bool = True,
    clip: bool = True,
    apply: bool = False,
) -> dict:
    """
    Add a Mirror modifier to an object across the given axis.

    Parameters:
    - object_name: Name of the mesh object to mirror.
    - axis: One of X, Y, Z.
    - merge: Weld coincident vertices at the mirror seam.
    - clip: Prevent vertices from crossing the mirror plane during transforms. Independent of merge.
    - apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.

    Returns the object's name, whether the modifier was applied, base vertex/edge/polygon
    counts, and (when apply=False) an "evaluated" count, "modifier" name, and world-space
    "bounds" reflecting the live modifier's effect.
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
) -> dict:
    """
    Add a linear Array modifier to an object, duplicating it along an offset direction.

    Parameters:
    - object_name: Name of the mesh object to array.
    - count: Number of copies (including the original).
    - relative_offset: [x, y, z] offset between copies, relative to the object's bounding box.
    - apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.

    Returns the object's name, whether the modifier was applied, base vertex/edge/polygon
    counts, and (when apply=False) an "evaluated" count, "modifier" name, and world-space
    "bounds" reflecting the live modifier's effect.
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
    pivot_object_name: str | None = None,
    pivot_location: tuple[float, float, float] | None = None,
    radius: float | None = None,
) -> dict:
    """
    Duplicate an object radially around a pivot, evenly spaced about an axis.

    The array's visible spread is the distance between the object and the pivot - if
    the mesh is centered on its own origin, every rotated copy lands on top of the
    original. Provide exactly one of pivot_object_name, pivot_location, or radius to
    set that distance; omitting all three raises an error instead of silently
    producing overlapping copies.

    Parameters:
    - object_name: Name of the mesh object to array.
    - count: Number of copies around the circle (including the original). Must be at least 2.
    - axis: One of X, Y, Z — the axis to rotate around.
    - apply: If True, bake the modifier into the mesh and remove the helper empty. If False (default), leave both live.
    - pivot_object_name: Name of an existing object whose world location is used as the pivot.
    - pivot_location: [x, y, z] world location to use as the pivot.
    - radius: Distance to auto-place the pivot from the object, perpendicular to axis. For a
      parented object, the pivot is offset from its world-space location, not its local one.

    Returns the object's name, whether the modifier was applied, base vertex/edge/polygon
    counts, and (when apply=False) an "evaluated" count, "modifier" name, and world-space
    "bounds" reflecting the live modifier's effect.
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
                "pivot_object_name": pivot_object_name,
                "pivot_location": list(pivot_location) if pivot_location else None,
                "radius": radius,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error creating radial array: {e}")
        raise ToolError(f"Error creating radial array: {e}") from e
