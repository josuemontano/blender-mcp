"""Persistent named-attribute and tagged instance-system handlers."""

# ruff: file-ignore[line-too-long]

from array import array
from typing import Any

import bpy

from ._shared import (
    ROLE_KEY,
    group_dependencies,
    require_group,
    require_object,
    serialize_value,
    set_input,
    set_modifier_input,
)
from .authoring import atomic_group_edit


def _attribute_container(obj):
    """Return the object's persistent geometry-attribute collection."""
    attributes = getattr(obj.data, "attributes", None)
    if attributes is None:
        raise ValueError(f"Object '{obj.name}' ({obj.type}) does not expose persistent attributes")
    return attributes


def _attribute_consumers(attribute_name: str) -> list[dict[str, str]]:
    """Find known Named Attribute and Store Named Attribute consumers by exact name."""
    consumers = []
    for group in bpy.data.node_groups:
        if group.bl_idname != "GeometryNodeTree":
            continue
        for node in group.nodes:
            if node.bl_idname not in {"GeometryNodeInputNamedAttribute", "GeometryNodeStoreNamedAttribute"}:
                continue
            name_socket = next((socket for socket in node.inputs if socket.name == "Name"), None)
            if name_socket is not None and not name_socket.is_linked and name_socket.default_value == attribute_name:
                consumers.append({"node_group": group.name, "node": node.name, "node_type": node.bl_idname})
    return consumers


def _component_property(data_type: str) -> tuple[str, int, str]:
    """Map Blender attribute data types to storage property, width, and foreach typecode."""
    mapping = {
        "FLOAT": ("value", 1, "f"),
        "INT": ("value", 1, "i"),
        "BOOLEAN": ("value", 1, "b"),
        "FLOAT_VECTOR": ("vector", 3, "f"),
        "FLOAT_COLOR": ("color", 4, "f"),
        "BYTE_COLOR": ("color", 4, "f"),
    }
    if data_type not in mapping:
        return ("value", 1, "f")
    return mapping[data_type]


def _read_attribute(attribute) -> list[Any]:
    """Read one attribute into JSON-compatible scalar or tuple values."""
    prop, width, typecode = _component_property(attribute.data_type)
    if attribute.data_type == "STRING":
        return [item.value for item in attribute.data]
    buffer = array(typecode, [0]) * (len(attribute.data) * width)
    attribute.data.foreach_get(prop, buffer)
    if width == 1:
        return list(buffer)
    return [list(buffer[index : index + width]) for index in range(0, len(buffer), width)]


def _write_attribute(attribute, values: list[Any]) -> None:
    """Write a fully validated attribute payload efficiently."""
    if len(values) != len(attribute.data):
        raise ValueError(
            f"Attribute '{attribute.name}' expects {len(attribute.data)} values for domain {attribute.domain}, got {len(values)}"
        )
    if attribute.data_type == "STRING":
        for item, value in zip(attribute.data, values, strict=True):
            item.value = str(value)
        return
    prop, width, typecode = _component_property(attribute.data_type)
    flattened = []
    for index, value in enumerate(values):
        components = [value] if width == 1 else list(value)
        if len(components) != width:
            raise ValueError(f"Attribute value {index} requires {width} components")
        flattened.extend(components)
    buffer = array(typecode, flattened)
    attribute.data.foreach_set(prop, buffer)


def _role_node(group, role: str):
    """Resolve exactly one tagged workflow node by its stable MCP role."""
    matches = [node for node in group.nodes if node.get(ROLE_KEY) == role]
    if len(matches) != 1:
        raise ValueError(f"Expected one '{role}' node in '{group.name}', found {len(matches)}")
    return matches[0]


def _instance_summary(group) -> dict[str, Any]:
    """Inspect a tagged scatter or array graph without inferring from labels."""
    instance = _role_node(group, "instance_on_points") if group.get("blender_mcp_builder") == "SCATTER" else None
    if instance is None:
        matches = [node for node in group.nodes if node.get(ROLE_KEY) in {"instance_on_points", "instance_on_faces"}]
        if len(matches) != 1:
            raise ValueError("The group is not a tagged scatter, array, or paneling instance system")
        instance = matches[0]
    source = (
        _role_node(group, "instance_source")
        if any(node.get(ROLE_KEY) == "instance_source" for node in group.nodes)
        else _role_node(group, "panel_source")
    )
    realize = [node for node in group.nodes if node.get(ROLE_KEY) == "realize_instances"]
    controls = {
        item.name: serialize_value(getattr(item, "default_value", None))
        for item in group.interface.items_tree
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name != "Geometry"
    }
    return {
        "node_group": group.name,
        "builder": group.get("blender_mcp_builder"),
        "instance_node": instance.name,
        "source_node": source.name,
        "source_type": source.bl_idname,
        "source_dependencies": group_dependencies(group),
        "pick_instance": serialize_value(
            next(socket for socket in instance.inputs if socket.name == "Pick Instance").default_value
        ),
        "rotation": serialize_value(
            next(socket for socket in instance.inputs if socket.name == "Rotation").default_value
        ),
        "scale": serialize_value(next(socket for socket in instance.inputs if socket.name == "Scale").default_value),
        "realized": bool(controls.get("Realize Instances", bool(realize))),
        "controls": controls,
        "nesting_depth": None,
        "estimated_instance_count": None,
    }


class GeometryNodesAttributeInstanceHandlersMixin:
    """Manage persistent attributes and stable builder-owned instance controls."""

    def manage_named_attributes(
        self,
        object_name,
        action,
        attribute_name=None,
        new_name=None,
        data_type=None,
        domain=None,
        values=None,
        confirm_destructive=False,
    ):
        obj = require_object(object_name)
        attributes = _attribute_container(obj)
        if action == "LIST":
            return {
                "object": obj.name,
                "attributes": [
                    {
                        "name": attribute.name,
                        "data_type": attribute.data_type,
                        "domain": attribute.domain,
                        "element_count": len(attribute.data),
                        "is_internal": attribute.is_internal,
                        "is_required": attribute.is_required,
                    }
                    for attribute in attributes
                ],
            }
        if not attribute_name:
            raise ValueError(f"{action} requires attribute_name")
        attribute = attributes.get(attribute_name)
        consumers = _attribute_consumers(attribute_name)
        if action == "CREATE":
            if attribute is not None:
                raise ValueError(f"Attribute already exists: {attribute_name}")
            if not data_type or not domain:
                raise ValueError("CREATE requires data_type and domain")
            attribute = attributes.new(name=attribute_name, type=data_type, domain=domain)
            if values is not None:
                _write_attribute(attribute, values)
        elif action == "SET":
            if attribute is None:
                raise ValueError(f"Attribute not found: {attribute_name}")
            if values is None:
                raise ValueError("SET requires values")
            _write_attribute(attribute, values)
        elif action == "RENAME":
            if attribute is None:
                raise ValueError(f"Attribute not found: {attribute_name}")
            if not new_name:
                raise ValueError("RENAME requires new_name")
            if attributes.get(new_name) is not None:
                raise ValueError(f"Attribute already exists: {new_name}")
            attribute.name = new_name
        elif action == "CONVERT":
            if not confirm_destructive:
                raise ValueError("confirm_destructive=True is required for CONVERT")
            if attribute is None or not data_type or not domain:
                raise ValueError("CONVERT requires an existing attribute, data_type, and domain")
            if domain != attribute.domain:
                raise ValueError("Cross-domain conversion requires an explicit resampling policy and is not performed")
            old_values = _read_attribute(attribute)
            temporary_name = f"{attribute_name}.__MCP_CONVERT__"
            converted = attributes.new(name=temporary_name, type=data_type, domain=domain)
            try:
                _write_attribute(converted, old_values)
            except Exception:
                attributes.remove(converted)
                raise
            attributes.remove(attribute)
            converted.name = new_name or attribute_name
            attribute = converted
        elif action == "REMOVE":
            if not confirm_destructive:
                raise ValueError("confirm_destructive=True is required for REMOVE")
            if attribute is None:
                raise ValueError(f"Attribute not found: {attribute_name}")
            if attribute.is_required:
                raise ValueError(f"Required built-in attribute cannot be removed: {attribute_name}")
            attributes.remove(attribute)
            attribute = None
        else:
            raise ValueError(f"Unsupported attribute action: {action}")
        if hasattr(obj.data, "update"):
            obj.data.update()
        return {
            "object": obj.name,
            "action": action,
            "attribute": None
            if attribute is None
            else {
                "name": attribute.name,
                "data_type": attribute.data_type,
                "domain": attribute.domain,
                "element_count": len(attribute.data),
            },
            "known_consumers": consumers,
            "changed_objects": [obj.name],
        }

    def manage_procedural_instances(
        self,
        node_group_name,
        source_type=None,
        source_name=None,
        pick_instance=None,
        rotation=None,
        scale=None,
        translation=None,
        realize_instances=None,
    ):
        group = require_group(node_group_name)
        has_edits = any(
            value is not None
            for value in [source_type, source_name, pick_instance, rotation, scale, translation, realize_instances]
        )
        if not has_edits:
            return _instance_summary(group)

        def edit_copy(working):
            summary = _instance_summary(working)
            instance = next(node for node in working.nodes if node.name == summary["instance_node"])
            source = next(node for node in working.nodes if node.name == summary["source_node"])
            control_updates = {}

            def set_control(name, value):
                item = next(
                    (
                        item
                        for item in working.interface.items_tree
                        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == name
                    ),
                    None,
                )
                if item is None:
                    raise ValueError(f"Builder '{working.name}' does not expose the '{name}' control")
                item.default_value = value
                control_updates[item.identifier] = value

            if source_name is not None:
                resolved_type = source_type or (
                    "OBJECT" if source.bl_idname == "GeometryNodeObjectInfo" else "COLLECTION"
                )
                expected = "GeometryNodeObjectInfo" if resolved_type == "OBJECT" else "GeometryNodeCollectionInfo"
                if source.bl_idname != expected:
                    raise ValueError(
                        "Changing source_type requires rebuilding the source node through patch_geometry_node_graph"
                    )
                collection = bpy.data.objects if resolved_type == "OBJECT" else bpy.data.collections
                value = collection.get(source_name)
                if value is None:
                    raise ValueError(f"{resolved_type.title()} source not found: {source_name}")
                set_input(source, "Object" if resolved_type == "OBJECT" else "Collection", value)
                set_control("Source" if resolved_type == "OBJECT" else "Panel Collection", value)
            if pick_instance is not None:
                set_input(instance, "Pick Instance", pick_instance)
            if rotation is not None:
                set_control("Instance Rotation", rotation)
            if scale is not None:
                set_control("Instance Scale", scale)
            if translation is not None:
                set_control("Instance Translation", translation)
            if realize_instances is not None:
                realization_control = next(
                    (
                        item
                        for item in working.interface.items_tree
                        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == "Realize Instances"
                    ),
                    None,
                )
                if realization_control is not None:
                    realization_control.default_value = realize_instances
                    control_updates[realization_control.identifier] = realize_instances
                elif realize_instances != summary["realized"]:
                    transform = _role_node(working, "translate_instances")
                    output = next(
                        node for node in working.nodes if node.bl_idname == "NodeGroupOutput" and node.is_active_output
                    )
                    for graph_link in list(working.links):
                        if graph_link.to_node == output:
                            working.links.remove(graph_link)
                    existing = [node for node in working.nodes if node.get(ROLE_KEY) == "realize_instances"]
                    if realize_instances:
                        realize = working.nodes.new("GeometryNodeRealizeInstances")
                        realize.name = "realize_instances"
                        realize[ROLE_KEY] = "realize_instances"
                        working.links.new(transform.outputs["Instances"], realize.inputs["Geometry"])
                        working.links.new(realize.outputs["Geometry"], output.inputs["Geometry"])
                    else:
                        for realize in existing:
                            working.nodes.remove(realize)
                        working.links.new(transform.outputs["Instances"], output.inputs["Geometry"])
            result = _instance_summary(working)
            result["control_updates"] = control_updates
            return result

        replacement, summary = atomic_group_edit(group, edit_copy)
        control_updates = summary.pop("control_updates", {})
        for obj in bpy.data.objects:
            for modifier in obj.modifiers:
                if modifier.type != "NODES" or modifier.node_group != replacement:
                    continue
                for identifier, value in control_updates.items():
                    set_modifier_input(modifier, identifier, value=value)
        summary["node_group"] = replacement.name
        summary["changed_resources"] = [replacement.name]
        return summary
