"""Atomic interface and node-graph authoring handlers."""

from collections.abc import Callable
from typing import Any

import bpy

from ._shared import (
    add_interface_socket,
    find_interface_item,
    find_panel,
    group_users,
    initialize_group,
    require_group,
    resolve_rna_value,
    resolve_socket,
    set_writable_property,
)


def _replace_group_references(original, replacement) -> None:
    """Redirect object modifiers and nested group nodes to an atomic replacement."""
    for obj in bpy.data.objects:
        for modifier in obj.modifiers:
            if modifier.type == "NODES" and modifier.node_group == original:
                modifier.node_group = replacement
    for tree in bpy.data.node_groups:
        if tree == original or not hasattr(tree, "nodes"):
            continue
        for node in tree.nodes:
            if getattr(node, "node_tree", None) == original:
                node.node_tree = replacement


def atomic_group_edit(group, edit: Callable[[Any], Any]):
    """Edit a private copy and swap it in only after every operation succeeds."""
    original_name = group.name
    working = group.copy()
    working.name = f"{original_name}.__MCP_WORKING__"
    try:
        result = edit(working)
    except Exception:
        bpy.data.node_groups.remove(working, do_unlink=True)
        raise
    _replace_group_references(group, working)
    bpy.data.node_groups.remove(group, do_unlink=True)
    working.name = original_name
    return working, result


def _node(group, name: str | None):
    """Resolve one exact node name in a working graph."""
    if not name:
        raise ValueError("A node name is required")
    node = group.nodes.get(name)
    if node is None:
        raise ValueError(f"Node '{name}' not found in '{group.name}'")
    return node


def _validate_node_type(bl_idname: str) -> None:
    """Allow only runtime-registered node families appropriate for Geometry Nodes."""
    allowed_prefixes = ("GeometryNode", "ShaderNode", "FunctionNode", "NodeFrame", "NodeGroupInput", "NodeGroupOutput")
    if not bl_idname.startswith(allowed_prefixes):
        raise ValueError(f"Node type '{bl_idname}' is outside the Geometry Nodes authoring allowlist")
    if getattr(bpy.types, bl_idname, None) is None:
        raise ValueError(f"Node type unavailable in this Blender runtime: {bl_idname}")


def _set_node_properties(node, properties: dict[str, Any]) -> None:
    """Apply runtime-verified direct properties to one node."""
    for key, value in properties.items():
        if key == "location":
            node.location = value
        elif key == "parent":
            raise ValueError("Use MOVE_TO_FRAME to set a node parent")
        else:
            set_writable_property(node, key, value)


def _apply_graph_operation(group, operation: dict[str, Any], name_map: dict[str, str]) -> None:
    """Apply one already schema-validated graph edit to a private working copy."""
    action = operation["operation"]
    if action == "ADD_NODE":
        bl_idname = operation.get("bl_idname")
        if not bl_idname:
            raise ValueError("ADD_NODE requires bl_idname")
        _validate_node_type(bl_idname)
        node = group.nodes.new(bl_idname)
        requested = operation.get("new_name") or operation.get("node_name")
        if requested:
            if group.nodes.get(requested) is not None and group.nodes.get(requested) != node:
                group.nodes.remove(node)
                raise ValueError(f"Node name already exists: {requested}")
            node.name = requested
        _set_node_properties(node, operation.get("properties", {}))
        name_map[requested or node.name] = node.name
        return
    node_name = operation.get("node_name")
    if action not in {"ADD_LINK", "REMOVE_LINK"} and not node_name:
        raise ValueError(f"{action} requires node_name")
    if action == "UPDATE_NODE":
        node = _node(group, node_name)
        new_name = operation.get("new_name")
        if new_name and new_name != node.name:
            if group.nodes.get(new_name) is not None:
                raise ValueError(f"Node name already exists: {new_name}")
            node.name = new_name
        _set_node_properties(node, operation.get("properties", {}))
    elif action == "SET_INPUT":
        node = _node(group, node_name)
        socket = resolve_socket(
            node.inputs,
            operation.get("socket_identifier"),
            operation.get("socket_index"),
            f"input on {node.name}",
        )
        if not hasattr(socket, "default_value"):
            raise ValueError(f"Input '{socket.identifier}' on '{node.name}' has no settable default")
        prop = socket.bl_rna.properties.get("default_value")
        value = resolve_rna_value(prop, operation.get("value")) if prop is not None else operation.get("value")
        socket.default_value = value
    elif action == "ADD_LINK":
        from_node = _node(group, operation.get("from_node"))
        to_node = _node(group, operation.get("to_node"))
        from_socket = resolve_socket(
            from_node.outputs,
            operation.get("from_socket_identifier"),
            operation.get("from_socket_index"),
            f"output on {from_node.name}",
        )
        to_socket = resolve_socket(
            to_node.inputs,
            operation.get("to_socket_identifier"),
            operation.get("to_socket_index"),
            f"input on {to_node.name}",
        )
        group.links.new(from_socket, to_socket)
    elif action == "REMOVE_LINK":
        from_node = _node(group, operation.get("from_node"))
        to_node = _node(group, operation.get("to_node"))
        from_socket = resolve_socket(
            from_node.outputs,
            operation.get("from_socket_identifier"),
            operation.get("from_socket_index"),
            f"output on {from_node.name}",
        )
        to_socket = resolve_socket(
            to_node.inputs,
            operation.get("to_socket_identifier"),
            operation.get("to_socket_index"),
            f"input on {to_node.name}",
        )
        matches = [link for link in group.links if link.from_socket == from_socket and link.to_socket == to_socket]
        if len(matches) != 1:
            raise ValueError(f"Expected one matching link, found {len(matches)}")
        group.links.remove(matches[0])
    elif action == "MOVE_TO_FRAME":
        node = _node(group, node_name)
        frame_name = operation.get("frame_name")
        node.parent = _node(group, frame_name) if frame_name else None
        if node.parent is not None and node.parent.bl_idname != "NodeFrame":
            raise ValueError(f"Node '{frame_name}' is not a frame")
    elif action == "REMOVE_NODE":
        group.nodes.remove(_node(group, node_name))
    elif action == "SET_ACTIVE_OUTPUT":
        node = _node(group, node_name)
        if not hasattr(node, "is_active_output"):
            raise ValueError(f"Node '{node.name}' cannot be an active output")
        node.is_active_output = True
    else:
        raise ValueError(f"Unsupported graph operation: {action}")


def _apply_interface_edit(group, edit: dict[str, Any], migration_policy: str) -> dict[str, Any]:
    """Apply one interface operation to a private working copy."""
    action = edit["operation"]
    if action == "ADD_SOCKET":
        spec = edit.get("socket")
        if spec is None:
            raise ValueError("ADD_SOCKET requires socket")
        panels = {item.name: item for item in group.interface.items_tree if item.item_type == "PANEL"}
        item = add_interface_socket(group, spec, panels)
        return {"operation": action, "identifier": item.identifier, "name": item.name}
    if action == "ADD_PANEL":
        spec = edit.get("panel")
        if spec is None:
            raise ValueError("ADD_PANEL requires panel")
        parent = find_panel(group, spec["parent_panel"]) if spec.get("parent_panel") else None
        item = group.interface.new_panel(
            name=spec["name"],
            description=spec.get("description", ""),
            default_closed=spec.get("default_closed", False),
            parent=parent,
        )
        return {"operation": action, "identifier": item.identifier, "name": item.name}
    identifier = edit.get("identifier")
    if not identifier:
        raise ValueError(f"{action} requires identifier")
    item = find_interface_item(group, identifier)
    if action == "UPDATE":
        changes = edit.get("changes", {})
        if (
            "socket_type" in changes
            and changes["socket_type"] != getattr(item, "socket_type", None)
            and migration_policy != "ALLOW_BREAKING"
        ):
            raise ValueError("Changing socket_type requires migration_policy='ALLOW_BREAKING'")
        for key, value in changes.items():
            set_writable_property(item, key, value)
    elif action == "MOVE":
        parent_identifier = edit.get("parent_identifier")
        position = edit.get("position", 0)
        if parent_identifier is not None:
            parent = find_interface_item(group, parent_identifier)
            if parent.item_type != "PANEL":
                raise ValueError("parent_identifier must identify a panel")
            group.interface.move_to_parent(item, parent, position)
        else:
            group.interface.move(item, position)
    elif action == "REMOVE":
        if migration_policy != "ALLOW_BREAKING":
            raise ValueError("Removing interface items requires migration_policy='ALLOW_BREAKING'")
        group.interface.remove(item, move_content_to_parent=False)
    else:
        raise ValueError(f"Unsupported interface operation: {action}")
    return {"operation": action, "identifier": identifier, "name": item.name if action != "REMOVE" else None}


class GeometryNodesAuthoringHandlersMixin:
    """Create reusable groups and perform copy-on-write graph/interface edits."""

    def create_geometry_node_group(
        self,
        name,
        execution_role="MODIFIER",
        geometry_types=None,
        tool_modes=None,
        sockets=None,
        panels=None,
        description="",
        color_tag="NONE",
        collision_policy="ERROR",
        purpose="custom procedural system",
    ):
        group, created = initialize_group(
            name,
            purpose=purpose,
            execution_role=execution_role,
            geometry_types=geometry_types,
            tool_modes=tool_modes,
            sockets=sockets,
            panels=panels,
            description=description,
            color_tag=color_tag,
            collision_policy=collision_policy,
        )
        return {
            "node_group": group.name,
            "created": created,
            "execution_role": execution_role,
            "interface": [
                {
                    "identifier": getattr(item, "identifier", None),
                    "name": item.name,
                    "item_type": item.item_type,
                    "direction": getattr(item, "in_out", None),
                    "socket_type": getattr(item, "socket_type", None),
                }
                for item in group.interface.items_tree
            ],
            "mcp_uuid": group.get("blender_mcp_uuid"),
            "changed_resources": [group.name] if created else [],
        }

    def edit_node_group_interface(self, node_group_name, edits, migration_policy="ERROR_ON_BREAKING"):
        group = require_group(node_group_name)
        affected_users = group_users(group)

        def edit_copy(working):
            return [_apply_interface_edit(working, edit, migration_policy) for edit in edits]

        replacement, changes = atomic_group_edit(group, edit_copy)
        return {
            "node_group": replacement.name,
            "changes": changes,
            "affected_users": affected_users,
            "migration_policy": migration_policy,
            "changed_resources": [replacement.name],
            "changed_objects": sorted({user["object"] for user in affected_users}),
        }

    def patch_geometry_node_graph(self, node_group_name, operations):
        group = require_group(node_group_name)
        users = group_users(group)

        def edit_copy(working):
            name_map = {}
            for operation in operations:
                _apply_graph_operation(working, operation, name_map)
            return name_map

        replacement, name_map = atomic_group_edit(group, edit_copy)
        return {
            "node_group": replacement.name,
            "operation_count": len(operations),
            "name_map": name_map,
            "node_count": len(replacement.nodes),
            "link_count": len(replacement.links),
            "changed_resources": [replacement.name],
            "changed_objects": sorted({user["object"] for user in users}),
        }
