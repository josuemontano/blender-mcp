"""Geometry Nodes tool execution and local asset-publication handlers."""

# ruff: file-ignore[line-too-long]

import re
import uuid

import bmesh
import bpy

from ...helpers import find_view3d, preserve_mode_and_selection
from ._shared import require_group, require_object

_OPERATOR_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def _validate_tool_targets(group, objects, mode: str) -> None:
    """Validate every object against the tool's type and mode flags."""
    type_flags = {
        "MESH": "is_type_mesh",
        "CURVE": "is_type_curve",
        "POINTCLOUD": "is_type_pointcloud",
        "GREASEPENCIL": "is_type_grease_pencil",
    }
    mode_flag = f"is_mode_{mode.lower()}"
    if not getattr(group, mode_flag, False):
        raise ValueError(f"Tool '{group.name}' is not enabled for {mode} mode")
    for obj in objects:
        flag = type_flags.get(obj.type)
        if flag is None or not getattr(group, flag, False):
            raise ValueError(f"Tool '{group.name}' is not enabled for {obj.type} objects")


def _validate_element_indices(obj, vertices, edges, faces) -> None:
    """Reject stale or out-of-range mesh selection indices before mode changes."""
    if obj.type != "MESH" and any(value is not None for value in (vertices, edges, faces)):
        raise ValueError("Element indices can be supplied only for mesh objects")
    for label, values, collection in (
        ("vertex", vertices, getattr(obj.data, "vertices", ())),
        ("edge", edges, getattr(obj.data, "edges", ())),
        ("face", faces, getattr(obj.data, "polygons", ())),
    ):
        for index in values or []:
            if index < 0 or index >= len(collection):
                raise ValueError(f"{label.title()} index {index} is out of range on '{obj.name}'")


def _select_mesh_elements(obj, vertices, edges, faces) -> None:
    """Select exact mesh elements in Edit Mode for a tool invocation."""
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    for element in (*bm.verts, *bm.edges, *bm.faces):  # pyright: ignore[reportGeneralTypeIssues]
        element.select = False
    for index in vertices or []:
        bm.verts[index].select = True
    for index in edges or []:
        bm.edges[index].select = True
    for index in faces or []:
        bm.faces[index].select = True
    bm.select_mode = {
        kind for kind, values in (("VERT", vertices), ("EDGE", edges), ("FACE", faces)) if values is not None
    } or {"VERT", "EDGE", "FACE"}
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def _tool_operator(group):
    """Resolve a dynamically registered Geometry Nodes tool operator."""
    identifier = group.node_tool_idname
    if not identifier or not _OPERATOR_ID.fullmatch(identifier):
        raise ValueError(
            f"Tool '{group.name}' needs a valid node_tool_idname such as 'geometry.my_tool'; publish it with operator_idname"
        )
    module_name, operator_name = identifier.split(".", 1)
    module = getattr(bpy.ops, module_name, None)
    operator = getattr(module, operator_name, None) if module is not None else None
    if operator is None:
        raise RuntimeError(
            f"Geometry Nodes tool operator '{identifier}' is not registered yet; publish the group and let Blender refresh tool assets"
        )
    return operator, identifier


class GeometryNodesAssetHandlersMixin:
    """Execute explicit node tools and publish local groups as discoverable assets."""

    def run_geometry_nodes_tool(
        self,
        node_group_name,
        object_names,
        mode="OBJECT",
        vertex_indices=None,
        edge_indices=None,
        face_indices=None,
        inputs=None,
        confirm_destructive=False,
    ):
        if not confirm_destructive:
            raise ValueError("confirm_destructive=True is required to run a Geometry Nodes tool")
        group = require_group(node_group_name)
        if not group.is_tool:
            raise ValueError(f"Node group '{group.name}' is not marked as a Geometry Nodes tool")
        if not object_names:
            raise ValueError("At least one object_name is required")
        objects = [require_object(name) for name in object_names]
        _validate_tool_targets(group, objects, mode)
        for obj in objects:
            _validate_element_indices(obj, vertex_indices, edge_indices, face_indices)
        operator, operator_idname = _tool_operator(group)
        before_counts = {
            obj.name: {
                "vertices": len(obj.data.vertices),
                "edges": len(obj.data.edges),
                "faces": len(obj.data.polygons),
            }
            for obj in objects
            if obj.type == "MESH"
        }
        area, region = find_view3d()
        if area is None or region is None:
            raise RuntimeError("A VIEW_3D area is required to run Geometry Nodes tools")
        with preserve_mode_and_selection(), bpy.context.temp_override(area=area, region=region):
            bpy.ops.object.select_all(action="DESELECT")
            for obj in objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = objects[0]
            if mode != "OBJECT":
                bpy.ops.object.mode_set(mode=mode)  # pyright: ignore[reportArgumentType]
                if mode == "EDIT":
                    for obj in objects:
                        _select_mesh_elements(obj, vertex_indices, edge_indices, face_indices)
            result = operator("EXEC_DEFAULT", **(inputs or {}))
            if "RUNNING_MODAL" in result:
                raise RuntimeError(f"Geometry Nodes tool '{operator_idname}' entered modal execution")
            if "FINISHED" not in result:
                raise RuntimeError(f"Geometry Nodes tool '{operator_idname}' returned {sorted(result)}")
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        after_counts = {
            obj.name: {
                "vertices": len(obj.data.vertices),
                "edges": len(obj.data.edges),
                "faces": len(obj.data.polygons),
            }
            for obj in objects
            if obj.type == "MESH"
        }
        return {
            "node_group": group.name,
            "operator_idname": operator_idname,
            "objects": [obj.name for obj in objects],
            "before_counts": before_counts,
            "after_counts": after_counts,
            "topology_indices_stale": True,
            "changed_objects": [obj.name for obj in objects],
        }

    def publish_procedural_asset(
        self,
        node_group_name,
        description=None,
        author=None,
        tags=None,
        catalog_id=None,
        fake_user=True,
        operator_idname=None,
    ):
        group = require_group(node_group_name)
        if not any(node.bl_idname == "NodeGroupOutput" and node.is_active_output for node in group.nodes):
            raise ValueError("Cannot publish a group without an active Group Output")
        if operator_idname is not None:
            if not group.is_tool:
                raise ValueError("operator_idname is valid only for a group marked as a Geometry Nodes tool")
            if not _OPERATOR_ID.fullmatch(operator_idname):
                raise ValueError("operator_idname must use lowercase 'module.operator_name' syntax")
            for other in bpy.data.node_groups:
                if other != group and getattr(other, "node_tool_idname", "") == operator_idname:
                    raise ValueError(f"Geometry Nodes tool operator identifier is already used: {operator_idname}")
        if catalog_id is not None:
            try:
                uuid.UUID(catalog_id)
            except ValueError as exc:
                raise ValueError("catalog_id must be an RFC 4122 UUID") from exc
        if description is not None:
            group.description = description
        group.use_fake_user = fake_user
        if operator_idname is not None:
            group.node_tool_idname = operator_idname
        if group.asset_data is None:
            group.asset_mark()
        metadata = group.asset_data
        if description is not None:
            metadata.description = description
        if author is not None:
            metadata.author = author
        if catalog_id is not None:
            metadata.catalog_id = catalog_id
        existing_tags = {tag.name for tag in metadata.tags}
        for tag_name in tags or []:
            if tag_name not in existing_tags:
                metadata.tags.new(tag_name)
                existing_tags.add(tag_name)
        return {
            "node_group": group.name,
            "is_asset": True,
            "fake_user": group.use_fake_user,
            "description": metadata.description,
            "author": metadata.author,
            "catalog_id": str(metadata.catalog_id) if metadata.catalog_id else None,
            "tags": sorted(tag.name for tag in metadata.tags),
            "operator_idname": group.node_tool_idname if group.is_tool else None,
            "saved_externally": False,
            "changed_resources": [group.name],
        }
