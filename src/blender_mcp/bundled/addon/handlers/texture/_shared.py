"""Shared Blender-side validation and shader graph helpers."""

import math
import os

import bpy

MANAGED_OWNER = "blender-mcp"
SUPPORTED_IMAGE_EXTENSIONS = {
    ".bmp",
    ".cin",
    ".dpx",
    ".exr",
    ".hdr",
    ".jpeg",
    ".jpg",
    ".png",
    ".psd",
    ".tga",
    ".tif",
    ".tiff",
    ".webp",
}
PRINCIPLED_INPUTS = {
    "base_color": ("Base Color",),
    "metallic": ("Metallic",),
    "roughness": ("Roughness",),
    "ior": ("IOR",),
    "transmission_weight": ("Transmission Weight", "Transmission"),
    "coat_weight": ("Coat Weight", "Coat"),
    "coat_roughness": ("Coat Roughness",),
    "sheen_weight": ("Sheen Weight", "Sheen"),
    "emission_color": ("Emission Color", "Emission"),
    "emission_strength": ("Emission Strength",),
    "alpha": ("Alpha",),
}


def required_name(value, label):
    """Return a stripped non-empty public name."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def material_by_name(name):
    """Resolve one material by exact name."""
    material = bpy.data.materials.get(required_name(name, "material_name"))
    if material is None:
        raise ValueError(f"Material not found: {name}")
    return material


def mesh_object(name):
    """Resolve one editable mesh object."""
    obj = bpy.data.objects.get(required_name(name, "object_name"))
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    if obj.type != "MESH":
        raise ValueError(f"Object '{name}' is {obj.type}, expected MESH")
    return obj


def validate_engine(value):
    """Map the stable public engine vocabulary to Blender RNA identifiers."""
    value = str(value).upper()
    if value not in {"BOTH", "CYCLES", "EEVEE", "BLENDER_EEVEE_NEXT"}:
        raise ValueError("target_engine must be BOTH, CYCLES, EEVEE, or BLENDER_EEVEE_NEXT")
    if value != "BOTH":
        runtime_engine("EEVEE" if "EEVEE" in value else value)
    return value


def runtime_engine(value):
    """Resolve a stable Cycles/Eevee name against the running Blender RNA."""
    identifiers = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    if value == "CYCLES" and value in identifiers:
        return value
    if value in {"EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"}:
        for identifier in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            if identifier in identifiers:
                return identifier
    raise ValueError(f"Render engine '{value}' is unavailable; runtime engines={sorted(identifiers)}")


def active_output(material):
    """Return the active material output, preferring the Cycles/Eevee-shared output."""
    if not material.use_nodes or material.node_tree is None:
        return None
    outputs = [node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial"]
    return next((node for node in outputs if getattr(node, "is_active_output", False)), outputs[0] if outputs else None)


def linked_principled(material):
    """Resolve the Principled shader directly feeding the active surface output."""
    output = active_output(material)
    if output is None:
        return None, None
    surface = output.inputs.get("Surface")
    if surface is None or not surface.is_linked:
        return output, None
    source = surface.links[0].from_node
    return output, source if source.bl_idname == "ShaderNodeBsdfPrincipled" else None


def socket_by_names(node, names):
    """Resolve a socket by RNA identifier first and user-facing name second."""
    wanted = set(names)
    for socket in node.inputs:
        if getattr(socket, "identifier", None) in wanted or socket.name in wanted:
            return socket
    raise ValueError(f"Node '{node.name}' has no input matching {sorted(wanted)}")


def serializable(value):
    """Convert common RNA/math values to JSON-safe data."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        return list(value)
    except TypeError:
        return str(value)


def socket_snapshot(socket):
    """Describe a node socket without evaluating a graph."""
    result = {
        "name": socket.name,
        "identifier": getattr(socket, "identifier", socket.name),
        "type": getattr(socket, "type", None),
        "linked": bool(socket.is_linked),
    }
    if hasattr(socket, "default_value"):
        result["default"] = serializable(socket.default_value)
    return result


def set_finite_socket(node, names, value, label):
    """Set one scalar/color socket after rejecting non-finite values."""
    values = value if isinstance(value, (tuple, list)) else (value,)
    if any(not math.isfinite(float(component)) for component in values):
        raise ValueError(f"{label} must contain only finite values")
    socket = socket_by_names(node, names)
    socket.default_value = value
    return socket.name


def validate_unit_color(value, label):
    """Reject a color that is not exactly four finite channels in [0, 1]."""
    if value is None or len(value) != 4 or any(not 0.0 <= float(component) <= 1.0 for component in value):
        raise ValueError(f"{label} must contain four values in [0, 1]")


def tag_node(node, role):
    """Mark a node as managed without relying on its mutable display label."""
    node["mcp_owner"] = MANAGED_OWNER
    node["mcp_texture_role"] = role


def managed_node(nodes, node_type, role, name):
    """Reuse one tagged node or create it with a collision-safe Blender name."""
    node = next(
        (item for item in nodes if item.get("mcp_owner") == MANAGED_OWNER and item.get("mcp_texture_role") == role),
        None,
    )
    if node is None:
        node = nodes.new(node_type)
        node.name = name
        tag_node(node, role)
    elif node.bl_idname != node_type:
        raise ValueError(f"Managed node role '{role}' has unexpected type {node.bl_idname}")
    return node


def image_path_missing(image):
    """Report whether a file-backed image's resolved path is absent."""
    if image.source not in {"FILE", "TILED", "SEQUENCE", "MOVIE"} or image.packed_file:
        return False
    filepath = bpy.path.abspath(image.filepath, library=image.library)
    return bool(filepath) and not os.path.exists(filepath.replace("<UDIM>", "1001"))
