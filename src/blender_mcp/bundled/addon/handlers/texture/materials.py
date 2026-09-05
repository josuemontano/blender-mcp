# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportUnhashable=false
"""Material inspection, authoring, assignment, mapping, and texture-set handlers."""

import os

import bpy

from ._shared import (
    MANAGED_OWNER,
    PRINCIPLED_INPUTS,
    active_output,
    linked_principled,
    managed_node,
    material_by_name,
    mesh_object,
    required_name,
    serializable,
    set_finite_socket,
    socket_by_names,
    socket_snapshot,
    tag_node,
    validate_engine,
    validate_unit_color,
)

_VOLUME_ABSORPTION_ROLE = "volume_absorption"

_MATERIAL_PRESETS = {
    "WATER": {
        "base_color": (0.92, 0.98, 1.0, 1.0),
        "transmission_weight": 1.0,
        "ior": 1.333,
        "roughness": 0.04,
        "volume_absorption_color": (0.75, 0.95, 1.0, 1.0),
        "volume_density": 0.02,
    },
    "GLASS": {
        "base_color": (1.0, 1.0, 1.0, 1.0),
        "transmission_weight": 1.0,
        "ior": 1.45,
        "roughness": 0.02,
        "volume_absorption_color": (1.0, 1.0, 1.0, 1.0),
        "volume_density": 0.0,
    },
    "OIL": {
        "base_color": (0.55, 0.32, 0.08, 1.0),
        "transmission_weight": 0.82,
        "ior": 1.47,
        "roughness": 0.12,
        "volume_absorption_color": (0.35, 0.12, 0.02, 1.0),
        "volume_density": 0.15,
    },
    "TINTED": {
        "base_color": (0.1, 0.45, 0.8, 1.0),
        "transmission_weight": 0.95,
        "ior": 1.36,
        "roughness": 0.08,
        "volume_absorption_color": (0.04, 0.22, 0.7, 1.0),
        "volume_density": 0.12,
    },
}


def _material_users(material):
    return sorted(obj.name for obj in bpy.data.objects if any(slot.material == material for slot in obj.material_slots))


def _principled_values(node):
    if node is None:
        return None
    values = {}
    for field, names in PRINCIPLED_INPUTS.items():
        try:
            values[field] = serializable(socket_by_names(node, names).default_value)
        except ValueError:
            pass
    return values


def _material_summary(material):
    _output, shader = linked_principled(material)
    images = set()
    if material.use_nodes and material.node_tree:
        images = {
            node.image.name
            for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeTexImage" and node.image
        }
    return {
        "name": material.name,
        "use_nodes": bool(material.use_nodes),
        "surface_shader": shader.bl_idname if shader else None,
        "principled": _principled_values(shader),
        "image_count": len(images),
        "assigned_objects": _material_users(material),
        "users": material.users,
    }


def _configure_volume_absorption(material, color, density):
    """Create, update, or remove the managed Volume Absorption node feeding the active output."""
    tree = material.node_tree
    existing = next(
        (
            node
            for node in tree.nodes
            if node.get("mcp_owner") == MANAGED_OWNER and node.get("mcp_texture_role") == _VOLUME_ABSORPTION_ROLE
        ),
        None,
    )
    if density is None or density <= 0:
        if existing is not None:
            tree.nodes.remove(existing)
        return None
    validate_unit_color(color, "volume_absorption_color")
    node = managed_node(tree.nodes, "ShaderNodeVolumeAbsorption", _VOLUME_ABSORPTION_ROLE, "PBR Volume Absorption")
    set_finite_socket(node, ("Color",), color, "volume_absorption_color")
    set_finite_socket(node, ("Density",), density, "volume_density")
    tree.links.new(node.outputs["Volume"], active_output(material).inputs["Volume"])
    return node.name


def _set_material_properties(material, patch, engine):
    warnings = []
    surface_keys = {
        "surface_render_method",
        "use_transparency_overlap",
        "use_raytrace_refraction",
        "thickness_mode",
        "use_backface_culling",
        "use_transparent_shadow",
    }
    for key in surface_keys & patch.keys():
        if not hasattr(material, key):
            raise ValueError(f"Material.{key} is unavailable in this Blender build")
        setattr(material, key, patch[key])
    mode = patch.get("displacement_mode")
    if mode:
        if engine != "CYCLES" and mode in {"DISPLACEMENT", "BOTH"}:
            raise ValueError("True displacement requires target_engine=CYCLES")
        if not hasattr(material, "displacement_method"):
            raise ValueError("Material.displacement_method is unavailable in this Blender build")
        material.displacement_method = mode
    if engine == "BOTH" and patch.get("transmission_weight", 0) > 0:
        warnings.append("Transmission can differ between Cycles and Eevee; validate with a dual-engine preview.")
    return warnings


class TextureMaterialHandlers:
    """Inspect and safely mutate PBR material graphs and assignments."""

    def list_materials(self, object_name=None, include_unassigned=True, limit=50, offset=0, **_ignored):
        if object_name:
            obj = mesh_object(object_name)
            materials = {slot.material for slot in obj.material_slots if slot.material}
        else:
            materials = set(bpy.data.materials)
        if not include_unassigned:
            materials = {material for material in materials if _material_users(material)}
        ordered = sorted(materials, key=lambda item: item.name.casefold())
        page = ordered[int(offset) : int(offset) + int(limit)]
        next_offset = int(offset) + len(page)
        return {
            "materials": [_material_summary(material) for material in page],
            "total": len(ordered),
            "offset": int(offset),
            "limit": int(limit),
            "truncated": next_offset < len(ordered),
            "next_offset": next_offset if next_offset < len(ordered) else None,
        }

    def inspect_material(self, material_name, node_limit=100, node_offset=0, link_limit=200, link_offset=0):
        material = material_by_name(material_name)
        if not material.use_nodes or material.node_tree is None:
            return {"material": _material_summary(material), "nodes": [], "links": [], "images": [], "uv_maps": []}
        tree = material.node_tree
        nodes = sorted(tree.nodes, key=lambda node: node.name.casefold())
        links = list(tree.links)
        node_page = nodes[int(node_offset) : int(node_offset) + int(node_limit)]
        link_page = links[int(link_offset) : int(link_offset) + int(link_limit)]
        image_names = sorted(
            {node.image.name for node in nodes if node.bl_idname == "ShaderNodeTexImage" and node.image}
        )
        uv_maps = sorted({node.uv_map for node in nodes if node.bl_idname == "ShaderNodeUVMap" and node.uv_map})
        unsupported = sorted(
            {node.bl_idname for node in nodes if node.bl_idname in {"ShaderNodeScript", "ShaderNodeTexPointDensity"}}
        )
        output = active_output(material)
        return {
            "material": _material_summary(material),
            "active_output": output.name if output else None,
            "nodes": [
                {
                    "name": node.name,
                    "label": node.label,
                    "bl_idname": node.bl_idname,
                    "inputs": [socket_snapshot(socket) for socket in node.inputs],
                    "outputs": [socket_snapshot(socket) for socket in node.outputs],
                }
                for node in node_page
            ],
            "links": [
                {
                    "from_node": link.from_node.name,
                    "from_socket": link.from_socket.identifier,
                    "to_node": link.to_node.name,
                    "to_socket": link.to_socket.identifier,
                }
                for link in link_page
            ],
            "images": image_names,
            "uv_maps": uv_maps,
            "unsupported_node_types": unsupported,
            "render_settings": {
                key: getattr(material, key)
                for key in (
                    "surface_render_method",
                    "use_transparency_overlap",
                    "use_raytrace_refraction",
                    "thickness_mode",
                    "use_backface_culling",
                    "use_transparent_shadow",
                    "displacement_method",
                )
                if hasattr(material, key)
            },
            "node_pagination": {
                "total": len(nodes),
                "offset": int(node_offset),
                "truncated": int(node_offset) + len(node_page) < len(nodes),
                "next_offset": int(node_offset) + len(node_page)
                if int(node_offset) + len(node_page) < len(nodes)
                else None,
            },
            "link_pagination": {
                "total": len(links),
                "offset": int(link_offset),
                "truncated": int(link_offset) + len(link_page) < len(links),
                "next_offset": int(link_offset) + len(link_page)
                if int(link_offset) + len(link_page) < len(links)
                else None,
            },
        }

    def create_pbr_material(
        self, material_name, target_engine="BOTH", settings=None, reuse_existing=False, preset=None
    ):
        name, engine = required_name(material_name, "material_name"), validate_engine(target_engine)
        existing = bpy.data.materials.get(name)
        if existing:
            if not reuse_existing:
                raise ValueError(f"Material already exists: {name}")
            return {"material": _material_summary(existing), "created": False, "changed_resources": []}
        if preset is not None and preset not in _MATERIAL_PRESETS:
            raise ValueError(f"Unknown material preset: {preset}")
        material = bpy.data.materials.new(name)
        try:
            material.use_nodes = True
            tree = material.node_tree
            tree.nodes.clear()
            output = tree.nodes.new("ShaderNodeOutputMaterial")
            output.name = "PBR Material Output"
            output.is_active_output = True
            shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
            shader.name = "PBR Principled BSDF"
            tag_node(output, "material_output")
            tag_node(shader, "principled_surface")
            tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
            defaults = {"base_color": (0.8, 0.8, 0.8, 1.0), "metallic": 0.0, "roughness": 0.5, "ior": 1.5, "alpha": 1.0}
            patch = defaults | dict(_MATERIAL_PRESETS[preset] if preset else {}) | dict(settings or {})
            for key, value in patch.items():
                if key in PRINCIPLED_INPUTS:
                    set_finite_socket(shader, PRINCIPLED_INPUTS[key], value, key)
            warnings = _set_material_properties(material, patch, engine)
            volume_node = _configure_volume_absorption(
                material, patch.get("volume_absorption_color"), patch.get("volume_density")
            )
        except Exception:
            bpy.data.materials.remove(material)
            raise
        return {
            "material": _material_summary(material),
            "created": True,
            "target_engine": engine,
            "preset": preset,
            "volume_node": volume_node,
            "warnings": warnings,
            "changed_resources": [material.name],
        }

    def configure_pbr_material(self, material_name, patch, target_engine="BOTH"):
        material, engine = material_by_name(material_name), validate_engine(target_engine)
        _output, shader = linked_principled(material)
        if shader is None:
            raise ValueError("The active Material Output is not directly fed by a Principled BSDF")
        patch = dict(patch or {})
        before = _principled_values(shader)
        for key, value in patch.items():
            if key in PRINCIPLED_INPUTS:
                set_finite_socket(shader, PRINCIPLED_INPUTS[key], value, key)
        warnings = _set_material_properties(material, patch, engine)
        volume_node = None
        if "volume_density" in patch:
            volume_node = _configure_volume_absorption(
                material, patch.get("volume_absorption_color"), patch["volume_density"]
            )
        normal_strength = patch.get("normal_strength")
        if normal_strength is not None:
            normal_input = socket_by_names(shader, ("Normal",))
            normal = normal_input.links[0].from_node if normal_input.is_linked else None
            if normal is not None and normal.bl_idname not in {"ShaderNodeNormalMap", "ShaderNodeBump"}:
                normal = None
            if normal is None:
                warnings.append(
                    "normal_strength was supplied but no direct Normal Map/Bump node feeds Principled Normal."
                )
            else:
                socket_by_names(normal, ("Strength",)).default_value = normal_strength
        return {
            "material": material.name,
            "target_engine": engine,
            "before": before,
            "after": _principled_values(shader),
            "volume_node": volume_node,
            "warnings": warnings,
            "changed_resources": [material.name],
        }

    def assign_material(self, material_name, object_names, mode="APPEND", slot_index=None, face_indices=None):
        material = material_by_name(material_name)
        objects = [mesh_object(name) for name in object_names]
        mode = str(mode).upper()
        if mode not in {"APPEND", "REPLACE_SLOT", "ASSIGN_FACES"}:
            raise ValueError("mode must be APPEND, REPLACE_SLOT, or ASSIGN_FACES")
        plans = []
        for obj in objects:
            if mode == "REPLACE_SLOT":
                if slot_index is None or not 0 <= int(slot_index) < len(obj.data.materials):
                    raise ValueError(f"slot_index is out of range for '{obj.name}'")
            requested_faces = list((face_indices or {}).get(obj.name, []))
            if mode == "ASSIGN_FACES":
                invalid = [
                    index
                    for index in requested_faces
                    if not isinstance(index, int) or not 0 <= index < len(obj.data.polygons)
                ]
                if invalid:
                    raise ValueError(f"Invalid face indices for '{obj.name}': {invalid[:10]}")
            plans.append((obj, requested_faces))
        assignments = []
        changed_objects = []
        for obj, requested_faces in plans:
            changed = False
            if mode == "APPEND":
                existing = next((i for i, item in enumerate(obj.data.materials) if item == material), None)
                if existing is None:
                    obj.data.materials.append(material)
                    existing = len(obj.data.materials) - 1
                    changed = True
            elif mode == "REPLACE_SLOT":
                existing = int(slot_index)
                if obj.data.materials[existing] != material:
                    obj.data.materials[existing] = material
                    changed = True
            else:
                existing = next((i for i, item in enumerate(obj.data.materials) if item == material), None)
                if existing is None:
                    obj.data.materials.append(material)
                    existing = len(obj.data.materials) - 1
                    changed = True
                for index in requested_faces:
                    if obj.data.polygons[index].material_index != existing:
                        obj.data.polygons[index].material_index = existing
                        changed = True
            if changed:
                changed_objects.append(obj.name)
            assignments.append({"object": obj.name, "slot_index": existing, "face_indices": requested_faces})
        return {
            "material": material.name,
            "mode": mode,
            "assignments": assignments,
            "changed_objects": changed_objects,
            "changed_resources": [material.name] if changed_objects else [],
        }

    def configure_texture_mapping(self, material_name, texture_node_names, settings):
        material = material_by_name(material_name)
        if not material.use_nodes:
            raise ValueError(f"Material '{material.name}' does not use nodes")
        tree, settings = material.node_tree, dict(settings or {})
        targets = []
        for name in texture_node_names:
            node = tree.nodes.get(name)
            if node is None or node.bl_idname != "ShaderNodeTexImage":
                raise ValueError(f"Image Texture node not found: {name}")
            targets.append(node)
        source_kind = settings.get("coordinate_source", "UV")
        if source_kind == "UV":
            source = managed_node(
                tree.nodes, "ShaderNodeUVMap", f"mapping:{source_kind}:{settings.get('uv_map_name')}", "PBR UV Map"
            )
            source.uv_map = settings["uv_map_name"]
            output_socket = source.outputs["UV"]
        else:
            source = managed_node(tree.nodes, "ShaderNodeTexCoord", f"mapping:{source_kind}", "PBR Texture Coordinates")
            if source_kind == "OBJECT":
                source.object = bpy.data.objects.get(settings.get("object_name"))
                if source.object is None:
                    raise ValueError(f"Mapping object not found: {settings.get('object_name')}")
            output_socket = source.outputs[source_kind.title()]
        mapping = managed_node(tree.nodes, "ShaderNodeMapping", f"mapping-transform:{source_kind}", "PBR Mapping")
        mapping.inputs["Location"].default_value = settings.get("location", (0, 0, 0))
        mapping.inputs["Rotation"].default_value = settings.get("rotation", (0, 0, 0))
        mapping.inputs["Scale"].default_value = settings.get("scale", (1, 1, 1))
        tree.links.new(output_socket, mapping.inputs["Vector"])
        for node in targets:
            node.projection = settings.get("projection", "FLAT")
            node.projection_blend = settings.get("projection_blend", 0)
            node.interpolation = settings.get("interpolation", "Linear")
            node.extension = settings.get("extension", "REPEAT")
            tree.links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return {
            "material": material.name,
            "texture_nodes": [node.name for node in targets],
            "source_node": source.name,
            "mapping_node": mapping.name,
            "changed_resources": [material.name],
        }

    def apply_pbr_texture_set(
        self,
        material_name,
        textures,
        target_engine="BOTH",
        uv_map_name="UVMap",
        normal_strength=1.0,
        height_strength=0.1,
        ao_display_strength=0.0,
        reuse_existing_images=True,
    ):
        material, engine = material_by_name(material_name), validate_engine(target_engine)
        _output, shader = linked_principled(material)
        if shader is None:
            raise ValueError("The active Material Output must be directly fed by a Principled BSDF")
        textures = dict(textures or {})
        for channel, path in textures.items():
            if not os.path.isfile(path):
                raise ValueError(f"Texture file for {channel} does not exist: {path}")
        tree, created_images, nodes = material.node_tree, [], {}
        try:
            uv = managed_node(tree.nodes, "ShaderNodeUVMap", f"texture-set:uv:{uv_map_name}", "PBR UV Map")
            uv.uv_map = uv_map_name
            for channel, path in textures.items():
                existing = bpy.data.images.get(os.path.basename(path)) if reuse_existing_images else None
                image = existing or bpy.data.images.load(path, check_existing=reuse_existing_images)
                if existing is None:
                    created_images.append(image)
                image.colorspace_settings.name = "sRGB" if channel in {"base_color", "emission"} else "Non-Color"
                node = managed_node(
                    tree.nodes,
                    "ShaderNodeTexImage",
                    f"texture-set:{channel}",
                    f"PBR {channel.replace('_', ' ').title()}",
                )
                node.image = image
                tree.links.new(uv.outputs["UV"], node.inputs["Vector"])
                nodes[channel] = node
            direct = {
                "base_color": "Base Color",
                "metallic": "Metallic",
                "roughness": "Roughness",
                "opacity": "Alpha",
                "emission": "Emission Color",
            }
            for channel, socket_name in direct.items():
                if channel in nodes:
                    tree.links.new(nodes[channel].outputs["Color"], socket_by_names(shader, (socket_name,)))
            if "glossiness" in nodes:
                invert = managed_node(
                    tree.nodes, "ShaderNodeMath", "texture-set:gloss-invert", "PBR Gloss to Roughness"
                )
                invert.operation = "SUBTRACT"
                invert.inputs[0].default_value = 1.0
                tree.links.new(nodes["glossiness"].outputs["Color"], invert.inputs[1])
                tree.links.new(invert.outputs[0], socket_by_names(shader, ("Roughness",)))
            packed = nodes.get("orm") or nodes.get("rma")
            ao_source = nodes.get("ambient_occlusion").outputs["Color"] if nodes.get("ambient_occlusion") else None
            if packed:
                separate = managed_node(
                    tree.nodes, "ShaderNodeSeparateColor", "texture-set:packed-separate", "PBR Packed Channels"
                )
                tree.links.new(packed.outputs["Color"], separate.inputs["Color"])
                if "orm" in nodes:
                    ao_source = separate.outputs["Red"]
                    tree.links.new(separate.outputs["Green"], socket_by_names(shader, ("Roughness",)))
                    tree.links.new(separate.outputs["Blue"], socket_by_names(shader, ("Metallic",)))
                else:
                    tree.links.new(separate.outputs["Red"], socket_by_names(shader, ("Roughness",)))
                    tree.links.new(separate.outputs["Green"], socket_by_names(shader, ("Metallic",)))
                    ao_source = separate.outputs["Blue"]
            if ao_source is not None and ao_display_strength:
                multiply = managed_node(
                    tree.nodes,
                    "ShaderNodeMixRGB",
                    "texture-set:ao-multiply",
                    "PBR AO Display Multiply",
                )
                multiply.blend_type = "MULTIPLY"
                multiply.inputs[0].default_value = float(ao_display_strength)
                base_source = nodes.get("base_color")
                if base_source:
                    tree.links.new(base_source.outputs["Color"], multiply.inputs[1])
                else:
                    multiply.inputs[1].default_value = socket_by_names(shader, ("Base Color",)).default_value
                tree.links.new(ao_source, multiply.inputs[2])
                tree.links.new(multiply.outputs["Color"], socket_by_names(shader, ("Base Color",)))
            normal_image = nodes.get("normal_opengl") or nodes.get("normal_directx")
            if normal_image:
                normal_source = normal_image.outputs["Color"]
                if "normal_directx" in nodes:
                    separate = managed_node(
                        tree.nodes, "ShaderNodeSeparateColor", "texture-set:normal-separate", "PBR DirectX Normal Split"
                    )
                    combine = managed_node(
                        tree.nodes, "ShaderNodeCombineColor", "texture-set:normal-combine", "PBR DirectX Normal Flip"
                    )
                    invert = managed_node(
                        tree.nodes, "ShaderNodeMath", "texture-set:normal-green-invert", "PBR DirectX Green Flip"
                    )
                    invert.operation = "SUBTRACT"
                    invert.inputs[0].default_value = 1.0
                    tree.links.new(normal_source, separate.inputs["Color"])
                    tree.links.new(separate.outputs["Red"], combine.inputs["Red"])
                    tree.links.new(separate.outputs["Green"], invert.inputs[1])
                    tree.links.new(invert.outputs[0], combine.inputs["Green"])
                    tree.links.new(separate.outputs["Blue"], combine.inputs["Blue"])
                    normal_source = combine.outputs["Color"]
                normal = managed_node(tree.nodes, "ShaderNodeNormalMap", "texture-set:normal-map", "PBR Normal Map")
                normal.inputs["Strength"].default_value = float(normal_strength)
                tree.links.new(normal_source, normal.inputs["Color"])
                tree.links.new(normal.outputs["Normal"], socket_by_names(shader, ("Normal",)))
            height = nodes.get("height") or nodes.get("displacement")
            warnings = []
            if height:
                bump = managed_node(tree.nodes, "ShaderNodeBump", "texture-set:bump", "PBR Height Bump")
                bump.inputs["Strength"].default_value = float(height_strength)
                tree.links.new(height.outputs["Color"], bump.inputs["Height"])
                tree.links.new(bump.outputs["Normal"], socket_by_names(shader, ("Normal",)))
                if engine == "CYCLES" and "displacement" in nodes:
                    output = active_output(material)
                    displacement = managed_node(
                        tree.nodes, "ShaderNodeDisplacement", "texture-set:displacement", "PBR Displacement"
                    )
                    displacement.inputs["Scale"].default_value = float(height_strength)
                    tree.links.new(height.outputs["Color"], displacement.inputs["Height"])
                    tree.links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])
                    material.displacement_method = "BOTH"
                elif "displacement" in nodes:
                    warnings.append(
                        "True displacement is unsupported for the requested Eevee-compatible graph; bump is used."
                    )
            if ("ambient_occlusion" in nodes or packed) and not ao_display_strength:
                warnings.append(
                    "AO remains unconnected because Principled BSDF has no AO input; "
                    "set ao_display_strength explicitly to opt in."
                )
        except Exception:
            for image in created_images:
                if image.users == 0:
                    bpy.data.images.remove(image)
            raise
        return {
            "material": material.name,
            "target_engine": engine,
            "channels": sorted(textures),
            "nodes": {key: value.name for key, value in nodes.items()},
            "images": sorted({node.image.name for node in nodes.values()}),
            "warnings": warnings,
            "changed_resources": [material.name, *sorted({node.image.name for node in nodes.values()})],
        }
