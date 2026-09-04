# ruff: file-ignore[line-too-long, magic-value-comparison, missing-return-type-private-function, missing-return-type-undocumented-public-function, missing-type-function-argument, no-self-use, too-many-arguments, too-many-branches, too-many-locals, too-many-positional-arguments, undocumented-public-method]
"""Blender-side handlers for typed scene composition and native geometry."""

import os

import bpy
import mathutils

from ..helpers import apply_modifier, modifier_result, rotation_as_native_list


def _required_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _object(name):
    obj = bpy.data.objects.get(_required_name(name, "object_name"))
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    return obj


def _scene_collection(name=None):
    if name is None:
        return bpy.context.collection
    collection = bpy.data.collections.get(_required_name(name, "collection_name"))
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def _transform_snapshot(obj):
    world_location, world_rotation, world_scale = obj.matrix_world.decompose()
    return {
        "location": list(obj.location),
        "rotation": rotation_as_native_list(obj),
        "rotation_mode": obj.rotation_mode,
        "scale": list(obj.scale),
        "matrix_world": [list(row) for row in obj.matrix_world],
        "world_location": list(world_location),
        "world_rotation_quaternion": list(world_rotation),
        "world_scale": list(world_scale),
    }


def _validate_mesh_topology(vertices, edges, faces):
    vertex_count = len(vertices)
    for label, records in (("edge", edges), ("face", faces)):
        for record_index, record in enumerate(records):
            if label == "edge" and len(record) != 2:
                raise ValueError(f"edge {record_index} must contain exactly two vertex indices")
            if label == "face" and len(record) < 3:
                raise ValueError(f"face {record_index} must contain at least three vertex indices")
            for index in record:
                if not isinstance(index, int) or index < 0 or index >= vertex_count:
                    raise ValueError(f"{label} {record_index} contains invalid vertex index {index}")


def _create_mesh(name, spec):
    vertices = spec.get("vertices", [])
    edges = spec.get("edges", [])
    faces = spec.get("faces", [])
    _validate_mesh_topology(vertices, edges, faces)
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, edges, faces)
    data.validate(verbose=False)
    data.update()
    return data


def _create_spline_data(name, spec):
    geometry_type = spec["kind"]
    data = bpy.data.curves.new(name, type=geometry_type)
    data.dimensions = spec.get("dimensions", "3D")
    data.resolution_u = spec.get("resolution_u", 12)
    data.bevel_depth = spec.get("bevel_depth", 0.0)
    data.bevel_resolution = spec.get("bevel_resolution", 4)
    data.extrude = spec.get("extrude", 0.0)
    for record in spec["splines"]:
        spline_type = record.get("type", "POLY")
        if geometry_type == "SURFACE" and spline_type == "BEZIER":
            raise ValueError("SURFACE geometry does not support BEZIER splines")
        spline = data.splines.new(spline_type)
        points = record["points"]
        if spline_type == "BEZIER":
            spline.bezier_points.add(len(points) - 1)
            for point, coordinate in zip(spline.bezier_points, points, strict=True):
                point.co = coordinate
                point.handle_left_type = "AUTO"
                point.handle_right_type = "AUTO"
        else:
            spline.points.add(len(points) - 1)
            for point, coordinate in zip(spline.points, points, strict=True):
                point.co = (*coordinate, 1.0)
            if spline_type == "NURBS":
                spline.order_u = min(record.get("order_u", 4), len(points))
                spline.use_endpoint_u = record.get("endpoint", True)
        spline.use_cyclic_u = record.get("cyclic", False)
    return data


def _create_font(name, spec):
    data = bpy.data.curves.new(name, type="FONT")
    data.body = spec["body"]
    data.size = spec.get("size", 1.0)
    data.extrude = spec.get("extrude", 0.0)
    data.bevel_depth = spec.get("bevel_depth", 0.0)
    data.align_x = spec.get("align_x", "LEFT")
    data.align_y = spec.get("align_y", "TOP_BASELINE")
    return data


def _create_meta(name, spec):
    data = bpy.data.metaballs.new(name)
    data.resolution = spec.get("resolution", 0.4)
    data.render_resolution = spec.get("render_resolution", 0.2)
    data.threshold = spec.get("threshold", 0.6)
    for record in spec["elements"]:
        element = data.elements.new()
        element.co = record["co"]
        element.radius = record.get("radius", 1.0)
        element.stiffness = record.get("stiffness", 2.0)
        element.type = record.get("type", "BALL")
    return data


def _create_lattice(name, spec):
    data = bpy.data.lattices.new(name)
    data.points_u = spec.get("points_u", 2)
    data.points_v = spec.get("points_v", 2)
    data.points_w = spec.get("points_w", 2)
    return data


def _create_pointcloud(name, spec):
    data = bpy.data.pointclouds.new(name)
    points = spec.get("points", [])
    data.resize(len(points))
    if points:
        data.attributes["position"].data.foreach_set("vector", [value for point in points for value in point])
    radii = spec.get("radii")
    if radii is not None:
        attribute = data.attributes.get("radius") or data.attributes.new("radius", "FLOAT", "POINT")
        attribute.data.foreach_set("value", radii)
    return data


def _create_volume(name, spec):
    path = os.path.abspath(spec["filepath"])
    if not os.path.isfile(path):
        raise ValueError(f"Volume file does not exist: {path}")
    if os.path.splitext(path)[1].lower() != ".vdb":
        raise ValueError("Volume filepath must use the .vdb extension")
    data = bpy.data.volumes.new(name)
    data.filepath = path
    data.is_sequence = spec.get("is_sequence", False)
    data.frame_start = spec.get("frame_start", 1)
    data.frame_duration = spec.get("frame_duration", 1)
    return data


_GEOMETRY_BUILDERS = {
    "MESH": _create_mesh,
    "CURVE": _create_spline_data,
    "SURFACE": _create_spline_data,
    "TEXT": _create_font,
    "META": _create_meta,
    "LATTICE": _create_lattice,
    "POINTCLOUD": _create_pointcloud,
    "VOLUME": _create_volume,
}


_CONSTRAINT_SETTINGS = {
    "COPY_LOCATION": {"use_x", "use_y", "use_z", "invert_x", "invert_y", "invert_z", "use_offset", "head_tail"},
    "COPY_ROTATION": {"use_x", "use_y", "use_z", "invert_x", "invert_y", "invert_z", "mix_mode", "euler_order"},
    "COPY_SCALE": {"use_x", "use_y", "use_z", "power", "use_make_uniform", "use_offset", "use_add"},
    "COPY_TRANSFORMS": {"mix_mode", "remove_target_shear"},
    "CHILD_OF": {
        "use_location_x",
        "use_location_y",
        "use_location_z",
        "use_rotation_x",
        "use_rotation_y",
        "use_rotation_z",
        "use_scale_x",
        "use_scale_y",
        "use_scale_z",
    },
    "DAMPED_TRACK": {"track_axis", "head_tail"},
    "TRACK_TO": {"track_axis", "up_axis", "use_target_z", "head_tail"},
    "LOCKED_TRACK": {"track_axis", "lock_axis", "head_tail"},
    "FOLLOW_PATH": {
        "offset",
        "offset_factor",
        "forward_axis",
        "up_axis",
        "use_curve_follow",
        "use_curve_radius",
        "use_fixed_location",
    },
    "CLAMP_TO": {"main_axis", "use_cyclic"},
    "LIMIT_LOCATION": {
        "use_min_x",
        "use_min_y",
        "use_min_z",
        "use_max_x",
        "use_max_y",
        "use_max_z",
        "min_x",
        "min_y",
        "min_z",
        "max_x",
        "max_y",
        "max_z",
        "use_transform_limit",
    },
    "LIMIT_ROTATION": {
        "use_limit_x",
        "use_limit_y",
        "use_limit_z",
        "min_x",
        "min_y",
        "min_z",
        "max_x",
        "max_y",
        "max_z",
        "euler_order",
        "use_transform_limit",
    },
    "LIMIT_SCALE": {
        "use_min_x",
        "use_min_y",
        "use_min_z",
        "use_max_x",
        "use_max_y",
        "use_max_z",
        "min_x",
        "min_y",
        "min_z",
        "max_x",
        "max_y",
        "max_z",
        "use_transform_limit",
    },
    "LIMIT_DISTANCE": {"distance", "limit_mode", "use_transform_limit"},
    "STRETCH_TO": {"rest_length", "bulge", "volume", "keep_axis", "head_tail"},
    "SHRINKWRAP": {
        "shrinkwrap_type",
        "wrap_mode",
        "wrap_method",
        "distance",
        "project_limit",
        "use_project_x",
        "use_project_y",
        "use_project_z",
        "use_negative_direction",
        "use_positive_direction",
        "cull_face",
    },
}


_MODIFIER_SETTINGS = {
    "ARRAY": {
        "count",
        "fit_type",
        "relative_offset_displace",
        "constant_offset_displace",
        "use_relative_offset",
        "use_constant_offset",
        "use_object_offset",
        "offset_object",
        "use_merge_vertices",
        "merge_threshold",
    },
    "BEVEL": {
        "width",
        "segments",
        "limit_method",
        "angle_limit",
        "affect",
        "profile",
        "vertex_group",
        "harden_normals",
    },
    "BOOLEAN": {"operation", "solver", "object", "collection", "operand_type", "use_self", "use_hole_tolerant"},
    "BUILD": {"frame_start", "frame_duration", "use_reverse", "use_random_order", "seed"},
    "CAST": {
        "cast_type",
        "factor",
        "radius",
        "size",
        "use_x",
        "use_y",
        "use_z",
        "use_radius_as_size",
        "object",
        "vertex_group",
    },
    "CURVE": {"object", "deform_axis", "vertex_group"},
    "DECIMATE": {
        "decimate_type",
        "ratio",
        "iterations",
        "angle_limit",
        "use_collapse_triangulate",
        "vertex_group",
        "vertex_group_factor",
    },
    "DISPLACE": {
        "strength",
        "mid_level",
        "direction",
        "space",
        "texture",
        "texture_coords",
        "texture_coords_object",
        "uv_layer",
        "vertex_group",
    },
    "LATTICE": {"object", "strength", "vertex_group"},
    "MASK": {"mode", "armature", "vertex_group", "invert_vertex_group", "threshold"},
    "MESH_DEFORM": {"object", "vertex_group", "precision", "use_dynamic_bind"},
    "MIRROR": {
        "use_axis",
        "use_bisect_axis",
        "use_bisect_flip_axis",
        "use_clip",
        "use_mirror_merge",
        "merge_threshold",
        "mirror_object",
    },
    "REMESH": {
        "mode",
        "octree_depth",
        "scale",
        "sharpness",
        "voxel_size",
        "use_smooth_shade",
        "use_remove_disconnected",
        "threshold",
    },
    "SCREW": {
        "axis",
        "angle",
        "steps",
        "render_steps",
        "iterations",
        "screw_offset",
        "object",
        "use_merge_vertices",
        "merge_threshold",
    },
    "SHRINKWRAP": {
        "target",
        "wrap_method",
        "wrap_mode",
        "offset",
        "project_limit",
        "use_project_x",
        "use_project_y",
        "use_project_z",
        "use_negative_direction",
        "use_positive_direction",
        "cull_face",
        "vertex_group",
    },
    "SIMPLE_DEFORM": {
        "deform_method",
        "deform_axis",
        "deform_angle",
        "deform_factor",
        "limits",
        "origin",
        "vertex_group",
        "invert_vertex_group",
    },
    "SKIN": {"use_smooth_shade", "branch_smoothing"},
    "SMOOTH": {"factor", "iterations", "use_x", "use_y", "use_z", "vertex_group"},
    "SOLIDIFY": {
        "thickness",
        "offset",
        "use_even_offset",
        "use_quality_normals",
        "material_offset",
        "material_offset_rim",
        "vertex_group",
    },
    "SUBSURF": {
        "subdivision_type",
        "levels",
        "render_levels",
        "quality",
        "uv_smooth",
        "boundary_smooth",
        "show_only_control_edges",
    },
    "TRIANGULATE": {"quad_method", "ngon_method", "min_vertices", "keep_custom_normals"},
    "WAVE": {
        "height",
        "width",
        "narrowness",
        "speed",
        "damping_time",
        "falloff_radius",
        "start_position_x",
        "start_position_y",
        "use_x",
        "use_y",
        "use_cyclic",
        "use_normal",
        "texture",
        "texture_coords_object",
        "uv_layer",
        "vertex_group",
    },
    "WELD": {"mode", "merge_threshold", "loose_edges", "vertex_group"},
    "WIREFRAME": {
        "thickness",
        "offset",
        "use_even_offset",
        "use_relative_offset",
        "use_boundary",
        "use_replace",
        "material_offset",
        "vertex_group",
    },
    "WEIGHTED_NORMAL": {"weight", "keep_sharp", "thresh", "mode", "vertex_group", "invert_vertex_group"},
    "UV_PROJECT": {"uv_layer", "aspect_x", "aspect_y", "scale_x", "scale_y"},
    "UV_WARP": {
        "object_from",
        "object_to",
        "bone_from",
        "bone_to",
        "uv_layer",
        "center",
        "axis_u",
        "axis_v",
        "vertex_group",
        "invert_vertex_group",
    },
    "VOLUME_TO_MESH": {"object", "grid_name", "threshold", "adaptivity"},
    "MESH_TO_VOLUME": {"object", "density", "voxel_amount", "voxel_size", "interior_band_width", "resolution_mode"},
    "OCEAN": {
        "geometry_mode",
        "resolution",
        "spatial_size",
        "wave_scale",
        "wave_scale_min",
        "wind_velocity",
        "wave_alignment",
        "wave_direction",
        "damping",
        "smallest_wave",
        "choppiness",
        "time",
        "spectrum",
        "fetch_jonswap",
        "sharpen_peak",
        "random_seed",
    },
}


def _resolve_setting(value):
    if not isinstance(value, dict) or set(value) != {"id_type", "name"}:
        return value
    collections = {
        "OBJECT": bpy.data.objects,
        "COLLECTION": bpy.data.collections,
        "TEXTURE": bpy.data.textures,
    }
    collection = collections.get(str(value["id_type"]).upper())
    if collection is None:
        raise ValueError(f"Unsupported ID reference type: {value['id_type']}")
    resolved = collection.get(value["name"])
    if resolved is None:
        raise ValueError(f"{value['id_type']} datablock not found: {value['name']}")
    return resolved


def _apply_allowlisted_settings(owner, settings, allowed):
    unknown = sorted(set(settings) - allowed)
    if unknown:
        raise ValueError(f"Unsupported settings for {owner.bl_rna.identifier}: {unknown}")
    prepared = []
    for name, value in settings.items():
        prop = owner.bl_rna.properties.get(name)
        if prop is None or prop.is_readonly:
            raise ValueError(f"Property '{name}' is not writable on {owner.bl_rna.identifier}")
        prepared.append((name, _resolve_setting(value)))
    previous = [(name, getattr(owner, name)) for name, _value in prepared]
    try:
        for name, value in prepared:
            setattr(owner, name, value)
    except Exception:
        for name, value in previous:
            try:
                setattr(owner, name, value)
            except Exception:
                pass
        raise


def _restore_properties(owner, values):
    for name, value in values.items():
        try:
            setattr(owner, name, value)
        except Exception:
            pass


class SceneHandlersMixin:
    """Provide native scene composition handlers."""

    def create_geometry_object(
        self, name, geometry, collection_name=None, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1)
    ):
        name = _required_name(name, "name")
        if bpy.data.objects.get(name) is not None:
            raise ValueError(f"Object already exists: {name}")
        kind = geometry.get("kind")
        builder = _GEOMETRY_BUILDERS.get(kind)
        if builder is None:
            raise ValueError(f"Unsupported geometry kind: {kind}")
        if any(value == 0 for value in scale):
            raise ValueError("scale components must be non-zero")
        data = builder(name, geometry)
        try:
            obj = bpy.data.objects.new(name, data)
            _scene_collection(collection_name).objects.link(obj)
            obj.location = location
            obj.rotation_euler = rotation
            obj.scale = scale
        except Exception:
            if (obj := bpy.data.objects.get(name)) is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            data_collection = {
                "MESH": bpy.data.meshes,
                "CURVE": bpy.data.curves,
                "SURFACE": bpy.data.curves,
                "TEXT": bpy.data.curves,
                "META": bpy.data.metaballs,
                "LATTICE": bpy.data.lattices,
                "POINTCLOUD": bpy.data.pointclouds,
                "VOLUME": bpy.data.volumes,
            }[kind]
            if data_collection.get(data.name) is not None:
                data_collection.remove(data)
            raise
        return {
            "name": obj.name,
            "type": obj.type,
            "data_name": data.name,
            "collection": next(iter(obj.users_collection)).name,
            "transform": _transform_snapshot(obj),
            "changed_objects": [obj.name],
            "changed_resources": [data.name],
        }

    def set_object_transform(self, object_name, patch, space="WORLD"):
        obj = _object(object_name)
        space = str(space).upper()
        if space not in {"LOCAL", "WORLD"}:
            raise ValueError("space must be LOCAL or WORLD")
        if "matrix" in patch:
            matrix = mathutils.Matrix(patch["matrix"])
            if space == "WORLD":
                obj.matrix_world = matrix
            else:
                obj.matrix_basis = matrix
        else:
            matrix = obj.matrix_world.copy() if space == "WORLD" else obj.matrix_basis.copy()
            location, rotation, scale = matrix.decompose()
            location = mathutils.Vector(patch.get("location", location))
            scale = mathutils.Vector(patch.get("scale", scale))
            if "rotation_euler" in patch:
                rotation = mathutils.Euler(patch["rotation_euler"], "XYZ").to_quaternion()
            elif "rotation_quaternion" in patch:
                rotation = mathutils.Quaternion(patch["rotation_quaternion"])
                if rotation.magnitude == 0:
                    raise ValueError("rotation_quaternion must be non-zero")
                rotation.normalize()
            elif "rotation_axis_angle" in patch:
                angle, x, y, z = patch["rotation_axis_angle"]
                axis = mathutils.Vector((x, y, z))
                if axis.length_squared == 0:
                    raise ValueError("rotation_axis_angle axis must be non-zero")
                rotation = mathutils.Quaternion(axis.normalized(), angle)
            result = mathutils.Matrix.LocRotScale(location, rotation, scale)
            if space == "WORLD":
                obj.matrix_world = result
            else:
                obj.matrix_basis = result
        return {"name": obj.name, **_transform_snapshot(obj)}

    def duplicate_or_instance_objects(
        self, source_object_name, names, transforms=None, mode="LINKED_DATA", collection_name=None
    ):
        source = _object(source_object_name)
        if len(names) != len(set(names)):
            raise ValueError("names must be unique")
        collisions = [name for name in names if bpy.data.objects.get(name) is not None]
        if collisions:
            raise ValueError(f"Objects already exist: {collisions}")
        collection = _scene_collection(collection_name)
        records = []
        if mode == "COLLECTION_INSTANCE" and not source.users_collection:
            raise ValueError(f"Source object '{source.name}' is not linked to a collection")
        created = []
        try:
            for index, name in enumerate(names):
                if mode == "COLLECTION_INSTANCE":
                    duplicate = bpy.data.objects.new(name, None)
                    duplicate.instance_type = "COLLECTION"
                    duplicate.instance_collection = source.users_collection[0]
                else:
                    duplicate = source.copy()
                    duplicate.name = name
                    if mode == "COPY" and source.data is not None:
                        duplicate.data = source.data.copy()
                created.append(duplicate)
                collection.objects.link(duplicate)
                transform = transforms[index] if transforms else None
                if transform:
                    if any(value == 0 for value in transform["scale"]):
                        raise ValueError(f"Scale components must be non-zero for '{name}'")
                    duplicate.location = transform["location"]
                    duplicate.rotation_euler = transform["rotation"]
                    duplicate.scale = transform["scale"]
                else:
                    duplicate.matrix_world = source.matrix_world.copy()
                records.append({"name": duplicate.name, "data": getattr(duplicate.data, "name", None)})
        except Exception:
            for duplicate in reversed(created):
                bpy.data.objects.remove(duplicate, do_unlink=True)
            raise
        return {"source": source.name, "mode": mode, "objects": records, "changed_objects": names}

    def manage_scene_collections(
        self,
        action,
        collection_name,
        object_names=None,
        parent_collection_name=None,
        hide_viewport=None,
        hide_render=None,
        confirm_remove=False,
    ):
        action = str(action).upper()
        collection = bpy.data.collections.get(collection_name)
        if action == "CREATE":
            if collection is not None:
                raise ValueError(f"Collection already exists: {collection_name}")
            parent = (
                bpy.data.collections.get(parent_collection_name)
                if parent_collection_name
                else bpy.context.scene.collection
            )
            if parent is None:
                raise ValueError(f"Parent collection not found: {parent_collection_name}")
            collection = bpy.data.collections.new(_required_name(collection_name, "collection_name"))
            parent.children.link(collection)
        elif collection is None:
            raise ValueError(f"Collection not found: {collection_name}")
        objects = [_object(name) for name in object_names or []]
        if action == "LINK_OBJECTS":
            for obj in objects:
                if collection.objects.get(obj.name) is None:
                    collection.objects.link(obj)
        elif action == "UNLINK_OBJECTS":
            invalid = [
                obj.name
                for obj in objects
                if collection.objects.get(obj.name) is not None and len(obj.users_collection) == 1
            ]
            if invalid:
                raise ValueError(f"Cannot unlink objects from their only collection: {invalid}")
            for obj in objects:
                if collection.objects.get(obj.name) is not None:
                    collection.objects.unlink(obj)
        elif action == "SET_VISIBILITY":
            if hide_viewport is None and hide_render is None:
                raise ValueError("SET_VISIBILITY requires hide_viewport or hide_render")
            if hide_viewport is not None:
                collection.hide_viewport = hide_viewport
            if hide_render is not None:
                collection.hide_render = hide_render
        elif action == "REMOVE":
            if not confirm_remove:
                raise ValueError("confirm_remove=True is required")
            if collection.objects or collection.children:
                raise ValueError("Collection must be empty before removal")
            bpy.data.collections.remove(collection)
            return {"removed": collection_name, "changed_resources": [collection_name]}
        elif action != "CREATE":
            raise ValueError(f"Unsupported collection action: {action}")
        return {
            "name": collection.name,
            "objects": [obj.name for obj in collection.objects],
            "hide_viewport": collection.hide_viewport,
            "hide_render": collection.hide_render,
            "changed_objects": [obj.name for obj in objects],
            "changed_resources": [collection.name],
        }

    def manage_object_hierarchy(self, assignments, preserve_world_transform=True):
        children = [_object(record["child_object_name"]) for record in assignments]
        if len({child.name for child in children}) != len(children):
            raise ValueError("Each child object may appear only once")
        planned = {}
        for record, child in zip(assignments, children, strict=True):
            parent_name = record.get("parent_object_name")
            parent = _object(parent_name) if parent_name else None
            if parent is child:
                raise ValueError(f"Object '{child.name}' cannot parent itself")
            bone_name = record.get("parent_bone_name")
            if bone_name and parent is None:
                raise ValueError(f"Bone parenting requires a parent object for '{child.name}'")
            if bone_name and parent.type != "ARMATURE":
                raise ValueError(f"Bone parenting requires an armature parent for '{child.name}'")
            if bone_name and parent.data.bones.get(bone_name) is None:
                raise ValueError(f"Bone not found: {bone_name}")
            planned[child] = parent
        for child, parent in planned.items():
            ancestor = parent
            seen = set()
            while ancestor is not None:
                if ancestor is child:
                    raise ValueError(f"Parenting '{child.name}' to '{parent.name}' would create a cycle")
                if ancestor in seen:
                    raise ValueError("Existing or requested parent graph contains a cycle")
                seen.add(ancestor)
                ancestor = planned.get(ancestor, ancestor.parent)
        for record, child in zip(assignments, children, strict=True):
            world = child.matrix_world.copy()
            parent_name = record.get("parent_object_name")
            child.parent = _object(parent_name) if parent_name else None
            child.parent_type = "BONE" if record.get("parent_bone_name") else "OBJECT"
            child.parent_bone = record.get("parent_bone_name") or ""
            if preserve_world_transform:
                child.matrix_world = world
        return {"assignments": assignments, "changed_objects": [obj.name for obj in children]}

    def manage_object_constraints(self, object_name, action, constraint, position=None):
        obj = _object(object_name)
        action = str(action).upper()
        name = constraint["name"]
        existing = obj.constraints.get(name)
        created = False
        if action == "ADD":
            if existing is not None:
                raise ValueError(f"Constraint already exists: {name}")
            item = obj.constraints.new(constraint["type"])
            item.name = name
            created = True
        else:
            if existing is None:
                raise ValueError(f"Constraint not found: {name}")
            item = existing
            if item.type != constraint["type"]:
                raise ValueError(f"Constraint '{name}' has type {item.type}, not {constraint['type']}")
        if action in {"ADD", "PATCH"}:
            settings = constraint.get("settings", {})
            previous = {
                "influence": item.influence,
                **{key: getattr(item, key) for key in settings if hasattr(item, key)},
            }
            if hasattr(item, "target"):
                previous["target"] = item.target
            if hasattr(item, "subtarget"):
                previous["subtarget"] = item.subtarget
            try:
                target_name = constraint.get("target_object_name")
                if target_name is not None:
                    if not hasattr(item, "target"):
                        raise ValueError(f"Constraint type {item.type} does not accept a target")
                    item.target = _object(target_name)
                if constraint.get("subtarget") is not None and hasattr(item, "subtarget"):
                    item.subtarget = constraint["subtarget"]
                item.influence = constraint.get("influence", 1.0)
                _apply_allowlisted_settings(item, settings, _CONSTRAINT_SETTINGS[item.type])
            except Exception:
                if created:
                    obj.constraints.remove(item)
                else:
                    _restore_properties(item, previous)
                raise
        elif action == "REMOVE":
            obj.constraints.remove(item)
            return {"object": obj.name, "removed": name}
        elif action == "MOVE":
            if position is None or position >= len(obj.constraints):
                raise ValueError("MOVE requires a valid position")
            obj.constraints.move(list(obj.constraints).index(item), position)
        else:
            raise ValueError(f"Unsupported constraint action: {action}")
        return {
            "object": obj.name,
            "constraint": item.name,
            "type": item.type,
            "stack_index": list(obj.constraints).index(item),
        }

    def manage_modifiers(self, object_name, action, modifier, position=None, confirm_destructive=False):
        obj = _object(object_name)
        action = str(action).upper()
        name = modifier["name"]
        existing = obj.modifiers.get(name)
        created = False
        if action == "ADD":
            if existing is not None:
                raise ValueError(f"Modifier already exists: {name}")
            item = obj.modifiers.new(name=name, type=modifier["type"])
            created = True
        else:
            if existing is None:
                raise ValueError(f"Modifier not found: {name}")
            item = existing
            if item.type != modifier["type"]:
                raise ValueError(f"Modifier '{name}' has type {item.type}, not {modifier['type']}")
        if action in {"ADD", "PATCH"}:
            settings = modifier.get("settings", {})
            previous = {key: getattr(item, key) for key in settings if hasattr(item, key)}
            try:
                _apply_allowlisted_settings(item, settings, _MODIFIER_SETTINGS[item.type])
            except Exception:
                if created:
                    obj.modifiers.remove(item)
                else:
                    _restore_properties(item, previous)
                raise
        elif action == "MOVE":
            if position is None or position >= len(obj.modifiers):
                raise ValueError("MOVE requires a valid position")
            obj.modifiers.move(list(obj.modifiers).index(item), position)
        elif action == "REMOVE":
            if not confirm_destructive:
                raise ValueError("confirm_destructive=True is required")
            obj.modifiers.remove(item)
            return {"name": obj.name, "removed_modifier": name}
        elif action == "APPLY":
            if not confirm_destructive:
                raise ValueError("confirm_destructive=True is required")
            apply_modifier(obj, item)
            return {"name": obj.name, "applied_modifier": name, "base_counts": _base_counts(obj)}
        else:
            raise ValueError(f"Unsupported modifier action: {action}")
        return {"name": obj.name, **modifier_result(obj, item, False)}

    def remove_scene_objects(self, object_names, confirm_remove=False):
        if not confirm_remove:
            raise ValueError("confirm_remove=True is required")
        if len(object_names) != len(set(object_names)):
            raise ValueError("object_names must be unique")
        objects = [_object(name) for name in object_names]
        dependencies = {
            obj.name: {
                "children": [child.name for child in obj.children],
                "collections": [collection.name for collection in obj.users_collection],
                "data": getattr(obj.data, "name", None),
            }
            for obj in objects
        }
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        return {"removed": object_names, "dependencies": dependencies, "changed_objects": object_names}


def _base_counts(obj):
    if obj.type != "MESH":
        return None
    return {"vertices": len(obj.data.vertices), "edges": len(obj.data.edges), "polygons": len(obj.data.polygons)}
