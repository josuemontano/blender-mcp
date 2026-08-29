"""Direct mesh-editing tools."""

import logging
from typing import Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok

logger = logging.getLogger("BlenderMCPServer")

PrimitiveType = Literal["CUBE", "SPHERE", "CYLINDER", "CONE", "TORUS", "PLANE", "CURVE"]
PrimitivePurpose = Literal["blockout"]


@mcp.tool()
async def mesh_create_primitive(
    ctx: Context,
    primitive_type: PrimitiveType,
    name: str | None = None,
    location: tuple[float, float, float] = (0, 0, 0),
    rotation: tuple[float, float, float] = (0, 0, 0),
    size: float = 1.0,
    dimensions: tuple[float, float, float] | None = None,
    purpose: PrimitivePurpose | None = None,
) -> dict:
    """Create a primitive mesh or curve object in the scene.

    Args:
        ctx: MCP request context.
        primitive_type: One of CUBE, SPHERE, CYLINDER, CONE, TORUS, PLANE, CURVE.
        name: Optional name for the created object. Defaults to Blender's auto-generated name.
        location: [x, y, z] location for the new object.
        rotation: [x, y, z] rotation in radians for the new object.
        size: Overall size (interpreted per primitive type, e.g. cube edge length, sphere radius).
        dimensions: Optional [x, y, z] world-space bounding box, applied after creation and overriding size for footprint - use this to get the same physical footprint across different primitive types.
        purpose: Set to "blockout" to tag the object as a placeholder proxy for later refinement.

    Returns:
        the created object's name, type, location, and mesh counts (plus dimensions/scale when `dimensions` was given).
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "create_primitive",
            {
                "primitive_type": primitive_type,
                "name": name,
                "location": list(location),
                "rotation": list(rotation),
                "size": size,
                "dimensions": list(dimensions) if dimensions is not None else None,
                "purpose": purpose,
            },
        )
        changed = [result.get("name")] if isinstance(result, dict) and result.get("name") else []
        return ok(result, changed_objects=changed)
    except Exception as e:
        logger.error(f"Error creating primitive: {e}")
        raise ToolError(f"Error creating primitive: {e}") from e


@mcp.tool()
async def mesh_extrude(
    ctx: Context,
    object_name: str,
    offset: tuple[float, float, float] = (0, 0, 1),
    face_indices: list[int] | None = None,
) -> dict:
    """Extrude the selected faces of a mesh object along an offset vector.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to edit.
        offset: [x, y, z] translation applied to the extruded geometry.
        face_indices: Optional list of face indices to extrude. If omitted, all faces are extruded. Use get_mesh_data(object_name, element_type="faces") to discover valid indices.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_extrude",
            {
                "object_name": object_name,
                "offset": list(offset),
                "face_indices": face_indices,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error extruding mesh: {e}")
        raise ToolError(f"Error extruding mesh: {e}") from e


@mcp.tool()
async def mesh_inset(
    ctx: Context,
    object_name: str,
    thickness: float = 0.05,
    depth: float = 0.0,
    face_indices: list[int] | None = None,
) -> dict:
    """Inset the selected faces of a mesh object, creating a smaller face surrounded by new faces.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to edit.
        thickness: Inset thickness.
        depth: Inset depth (pushes the inset faces along their normal).
        face_indices: Optional list of face indices to inset. If omitted, all faces are inset. Use get_mesh_data(object_name, element_type="faces") to discover valid indices.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_inset",
            {
                "object_name": object_name,
                "thickness": thickness,
                "depth": depth,
                "face_indices": face_indices,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error insetting mesh faces: {e}")
        raise ToolError(f"Error insetting mesh faces: {e}") from e


@mcp.tool()
async def mesh_bevel(
    ctx: Context,
    object_name: str,
    offset: float = 0.05,
    segments: int = 1,
    affect: Literal["EDGES", "VERTICES"] = "EDGES",
    edge_indices: list[int] | None = None,
    vertex_indices: list[int] | None = None,
) -> dict:
    """Bevel the selected edges or vertices of a mesh object.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to edit.
        offset: Bevel width.
        segments: Number of bevel segments.
        affect: "EDGES" or "VERTICES".
        edge_indices: Optional list of edge indices to bevel. Use get_mesh_data(object_name, element_type="edges") to discover valid indices.
        vertex_indices: Optional list of vertex indices to bevel. Use get_mesh_data(object_name, element_type="vertices") to discover valid indices. - If neither edge_indices nor vertex_indices is given, the whole mesh is selected.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_bevel",
            {
                "object_name": object_name,
                "offset": offset,
                "segments": segments,
                "affect": affect,
                "edge_indices": edge_indices,
                "vertex_indices": vertex_indices,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error beveling mesh: {e}")
        raise ToolError(f"Error beveling mesh: {e}") from e


@mcp.tool()
async def mesh_bridge(ctx: Context, object_name: str, edge_indices: list[int]) -> dict:
    """Bridge two open edge loops of a mesh object with new faces.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to edit.
        edge_indices: Required list of edge indices forming the two loops to bridge. Use get_mesh_data(object_name, element_type="edges") to discover valid indices.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_bridge",
            {
                "object_name": object_name,
                "edge_indices": edge_indices,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error bridging mesh edge loops: {e}")
        raise ToolError(f"Error bridging mesh edge loops: {e}") from e


SymmetrizeDirection = Literal["NEGATIVE_X", "POSITIVE_X", "NEGATIVE_Y", "POSITIVE_Y", "NEGATIVE_Z", "POSITIVE_Z"]


@mcp.tool()
async def mesh_symmetrize(ctx: Context, object_name: str, direction: SymmetrizeDirection = "NEGATIVE_X") -> dict:
    """Symmetrize a mesh across an axis, mirroring one half of the geometry onto the other.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to edit.
        direction: Which half to keep and mirror from, e.g. "NEGATIVE_X" keeps the -X half and mirrors it onto +X.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_symmetrize",
            {
                "object_name": object_name,
                "direction": direction,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error symmetrizing mesh: {e}")
        raise ToolError(f"Error symmetrizing mesh: {e}") from e


@mcp.tool()
async def mesh_boolean(
    ctx: Context,
    object_name: str,
    cutter_object_name: str,
    operation: Literal["UNION", "DIFFERENCE", "INTERSECT"] = "DIFFERENCE",
    keep_cutter: bool = True,
) -> dict:
    """Apply a boolean operation between two mesh objects.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object the boolean is applied to (the result).
        cutter_object_name: Name of the other mesh object used as the cutter/operand. Must differ from object_name.
        operation: One of UNION, DIFFERENCE, INTERSECT.
        keep_cutter: If True (default), the cutter object is kept after the operation is applied. Set False to delete it.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_boolean",
            {
                "object_name": object_name,
                "cutter_object_name": cutter_object_name,
                "operation": operation,
                "keep_cutter": keep_cutter,
            },
        )
        changed = [object_name] + ([] if keep_cutter else [cutter_object_name])
        return ok(result, changed_objects=changed)
    except Exception as e:
        logger.error(f"Error applying mesh boolean: {e}")
        raise ToolError(f"Error applying mesh boolean: {e}") from e


@mcp.tool()
async def mesh_subdivide(
    ctx: Context,
    object_name: str,
    cuts: int = 1,
    face_indices: list[int] | None = None,
) -> dict:
    """Subdivide the selected faces of a mesh object, adding more geometry.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to edit.
        cuts: Number of cuts per edge.
        face_indices: Optional list of face indices to subdivide. If omitted, all faces are subdivided. Use get_mesh_data(object_name, element_type="faces") to discover valid indices.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_subdivide",
            {
                "object_name": object_name,
                "cuts": cuts,
                "face_indices": face_indices,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error subdividing mesh: {e}")
        raise ToolError(f"Error subdividing mesh: {e}") from e


@mcp.tool()
async def mesh_remesh(ctx: Context, object_name: str, voxel_size: float = 0.1) -> dict:
    """Voxel-remesh a mesh object, rebuilding its topology at a uniform resolution.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to remesh.
        voxel_size: Size of the voxels used to rebuild the mesh; smaller values produce more detail.

    Returns:
        the object's name and updated vertex/edge/polygon counts.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_remesh",
            {
                "object_name": object_name,
                "voxel_size": voxel_size,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error remeshing mesh: {e}")
        raise ToolError(f"Error remeshing mesh: {e}") from e


@mcp.tool()
async def mesh_solidify(
    ctx: Context,
    object_name: str,
    thickness: float = 0.01,
    apply: bool = False,
) -> dict:
    """Give a mesh's surface thickness via a Solidify modifier.

    Args:
        ctx: MCP request context.
        object_name: Name of the mesh object to solidify.
        thickness: Thickness to add.
        apply: If True, bake the modifier into the mesh. If False (default), leave it as a live modifier.

    Returns:
        the object's name, whether the modifier was applied, base vertex/edge/polygon counts, and (when apply=False) an "evaluated" count, "modifier" name, and world-space "bounds" reflecting the live modifier's effect.
    Raises:
        ToolError: If the operation cannot be completed.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "mesh_solidify",
            {
                "object_name": object_name,
                "thickness": thickness,
                "apply": apply,
            },
        )
        return ok(result, changed_objects=[object_name])
    except Exception as e:
        logger.error(f"Error solidifying mesh: {e}")
        raise ToolError(f"Error solidifying mesh: {e}") from e
