"""Blender-main-thread handlers for production camera and rig workflows."""

import math
import uuid

import bpy
import mathutils

from ..helpers import paginate, rotation_as_native_list

_CAMERA_OPTICS = {
    "lens",
    "ortho_scale",
    "sensor_width",
    "sensor_height",
    "sensor_fit",
    "shift_x",
    "shift_y",
    "clip_start",
    "clip_end",
    "panorama_type",
}
_CAMERA_DISPLAY = {
    "passepartout_alpha",
    "show_passepartout",
    "show_safe_areas",
    "show_name",
    "show_limits",
    "show_mist",
    "show_composition_center",
    "show_composition_center_diagonal",
    "show_composition_golden",
    "show_composition_golden_tria_a",
    "show_composition_golden_tria_b",
    "show_composition_harmony_tri_a",
    "show_composition_harmony_tri_b",
    "show_composition_thirds",
}
_DOF_FIELDS = {
    "use_dof",
    "aperture_fstop",
    "aperture_blades",
    "aperture_rotation",
    "aperture_ratio",
}
_TRACK_CONSTRAINTS = {"TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK"}
_RIG_SCHEMA_VERSION = 1
_MAX_RIG_DESCENDANTS = 2_000
_MAX_ANIMATION_RECORDS = 5_000
_MIN_FRAME = -1_048_574
_MAX_FRAME = 1_048_574


def _finite_number(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _vector(value, label):
    if value is None or len(value) != 3:
        raise ValueError(f"{label} must contain exactly three numbers")
    return mathutils.Vector(
        tuple(_finite_number(component, f"{label}[{index}]") for index, component in enumerate(value))
    )


def _positive(value, label, *, allow_zero=False):
    value = _finite_number(value, label)
    if value < 0 if allow_zero else value <= 0:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be {qualifier}")
    return value


def _required_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _bounded_int(value, label, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    integer = int(value)
    if integer != value or not minimum <= integer <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return integer


def _scene(name):
    scene = bpy.data.scenes.get(name)
    if scene is None:
        raise ValueError(f"Scene not found: {name}")
    return scene


def _object(name, *, scene=None):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    if scene is not None and obj.name not in scene.objects:
        raise ValueError(f"Object '{name}' is not linked to scene '{scene.name}'")
    return obj


def _camera(name, *, scene=None):
    obj = _object(name, scene=scene)
    if obj.type != "CAMERA" or obj.data is None:
        raise ValueError(f"Object '{name}' is not a camera (type={obj.type})")
    _update_view_layer()
    return obj


def _ensure_collection(scene, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("collection_name must be a non-empty string")
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    return collection


def _plain(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, "name"):
        return value.name
    try:
        return list(value)
    except TypeError:
        return str(value)


def _update_view_layer():
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()


def _matrix_list(matrix):
    return [[float(value) for value in row] for row in matrix]


def _transform_info(obj):
    _update_view_layer()
    world_location, world_rotation, world_scale = obj.matrix_world.decompose()
    return {
        "local": {
            "location": list(obj.location),
            "rotation_mode": obj.rotation_mode,
            "rotation": rotation_as_native_list(obj),
            "scale": list(obj.scale),
            "matrix": _matrix_list(obj.matrix_basis),
        },
        "world": {
            "location": list(world_location),
            "rotation_quaternion": list(world_rotation),
            "scale": list(world_scale),
            "matrix": _matrix_list(obj.matrix_world),
        },
    }


def _constraint_info(constraint):
    result = {
        "name": constraint.name,
        "type": constraint.type,
        "influence": float(constraint.influence),
        "mute": bool(constraint.mute),
        "is_valid": bool(constraint.is_valid),
        "owner_space": getattr(constraint, "owner_space", None),
        "target_space": getattr(constraint, "target_space", None),
    }
    for field in (
        "target",
        "subtarget",
        "track_axis",
        "up_axis",
        "lock_axis",
        "forward_axis",
        "use_curve_follow",
        "use_fixed_location",
        "offset_factor",
    ):
        if hasattr(constraint, field):
            result[field] = _plain(getattr(constraint, field))
    return result


def _driver_records(obj):
    animation = getattr(obj, "animation_data", None)
    records = []
    for driver_curve in getattr(animation, "drivers", ()) if animation is not None else ():
        driver = driver_curve.driver
        variables = []
        for variable in driver.variables:
            variables.append(
                {
                    "name": variable.name,
                    "type": variable.type,
                    "targets": [
                        {
                            "id": getattr(target.id, "name", None),
                            "data_path": target.data_path,
                            "bone_target": target.bone_target,
                            "transform_type": target.transform_type,
                            "transform_space": target.transform_space,
                        }
                        for target in variable.targets
                    ],
                }
            )
        records.append(
            {
                "data_path": driver_curve.data_path,
                "array_index": driver_curve.array_index,
                "expression": driver.expression,
                "type": driver.type,
                "variables": variables,
            }
        )
    return records


def _action_records(owner_label, owner, max_records=None):
    if max_records is not None and max_records <= 0:
        return []
    animation = getattr(owner, "animation_data", None)
    action = getattr(animation, "action", None) if animation is not None else None
    if action is None:
        return []
    records = []
    curves = getattr(action, "fcurves", ())
    for curve in curves:
        if max_records is not None and len(records) >= max_records:
            break
        records.append(
            {
                "owner": owner_label,
                "action": action.name,
                "data_path": curve.data_path,
                "array_index": curve.array_index,
                "keyframes": len(curve.keyframe_points),
            }
        )
    if not records:
        records.append(
            {
                "owner": owner_label,
                "action": action.name,
                "frame_range": list(action.frame_range),
                "layered": bool(getattr(action, "is_action_layered", False)),
            }
        )
    return records


def _camera_settings(data):
    fields = ["type", *_CAMERA_OPTICS, *_CAMERA_DISPLAY]
    settings = {field: _plain(getattr(data, field)) for field in fields if hasattr(data, field)}
    dof = data.dof
    settings["dof"] = {
        field: _plain(getattr(dof, field))
        for field in [*_DOF_FIELDS, "focus_object", "focus_distance"]
        if hasattr(dof, field)
    }
    return settings


def _tag(obj, rig_id, role):
    obj["mcp_camera_rig_id"] = rig_id
    obj["mcp_camera_role"] = role
    obj["mcp_camera_schema_version"] = _RIG_SCHEMA_VERSION
    obj["mcp_camera_owner"] = "blender-mcp"


def _rig_metadata(obj):
    # Blender ID datablocks expose custom properties through keys(), but are not
    # themselves iterable like a normal mapping.
    return {
        key: _plain(obj[key])
        for key in obj.keys()  # ruff: ignore[in-dict-keys]
        if key.startswith("mcp_camera_")
    }


def _descendants(root, max_depth):
    found = []
    frontier = [(root, 0)]
    while frontier:
        parent, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for child in sorted(parent.children, key=lambda item: item.name):
            if len(found) >= _MAX_RIG_DESCENDANTS:
                return found, True
            found.append((child, depth + 1))
            frontier.append((child, depth + 1))
    return found, False


def _parent_hierarchy(obj):
    names = []
    parent = obj.parent
    while parent is not None:
        names.append(parent.name)
        parent = parent.parent
    return names


def _look_quaternion(origin, target):
    direction = target - origin
    if direction.length_squared <= 1e-16:
        raise ValueError("Camera and aim target cannot occupy the same world position")
    return direction.to_track_quat("-Z", "Y")


def _target_world_point(target, subtarget=None):
    if not subtarget:
        return target.matrix_world.translation.copy()
    bones = getattr(getattr(target, "pose", None), "bones", None)
    bone = bones.get(subtarget) if bones is not None else None
    if target.type != "ARMATURE" or bone is None:
        raise ValueError(f"Bone subtarget '{subtarget}' does not exist on armature target '{target.name}'")
    return target.matrix_world @ bone.matrix.translation


def _set_world_rotation(obj, rotation):
    location, _old_rotation, scale = obj.matrix_world.decompose()
    obj.matrix_world = mathutils.Matrix.LocRotScale(location, rotation, scale)


def _patch_values(owner, patch, allowed):
    patch = patch or {}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"Unsupported fields: {sorted(unknown)}")
    missing = [field for field in patch if not hasattr(owner, field)]
    if missing:
        raise ValueError(f"Running Blender does not support fields: {missing}")
    old = {field: _plain(getattr(owner, field)) for field in patch}
    assigned = []
    try:
        for field, value in patch.items():
            setattr(owner, field, value)
            assigned.append(field)
    except Exception:
        for field in reversed(assigned):
            setattr(owner, field, old[field])
        raise
    new = {field: _plain(getattr(owner, field)) for field in patch}
    return old, new


def _validate_optics(data, patch):
    patch = dict(patch or {})
    if "projection" in patch:
        patch["type"] = patch.pop("projection")
    allowed = _CAMERA_OPTICS | {"type"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"Unsupported camera optical fields: {sorted(unknown)}")
    for field in ("lens", "ortho_scale", "sensor_width", "sensor_height", "clip_start", "clip_end"):
        if field in patch:
            _positive(patch[field], field)
    for field in ("shift_x", "shift_y"):
        if field in patch:
            _finite_number(patch[field], field)
    projection = patch.get("type", data.type)
    if projection not in {"PERSP", "ORTHO", "PANO"}:
        raise ValueError("projection must be PERSP, ORTHO, or PANO")
    clip_start = patch.get("clip_start", data.clip_start)
    clip_end = patch.get("clip_end", data.clip_end)
    if clip_start >= clip_end:
        raise ValueError("clip_start must be less than clip_end")
    if "panorama_type" in patch and projection != "PANO":
        raise ValueError("panorama_type requires projection='PANO'")
    return patch


def _validate_display(patch):
    patch = dict(patch or {})
    unknown = set(patch) - _CAMERA_DISPLAY
    if unknown:
        raise ValueError(f"Unsupported camera display fields: {sorted(unknown)}")
    if "passepartout_alpha" in patch:
        alpha = _finite_number(patch["passepartout_alpha"], "passepartout_alpha")
        if not 0 <= alpha <= 1:
            raise ValueError("passepartout_alpha must be between 0 and 1")
    return patch


def _add_constraint(
    camera,
    target,
    *,
    name,
    constraint_type,
    track_axis="TRACK_NEGATIVE_Z",
    up_axis="UP_Y",
    lock_axis="LOCK_Y",
    influence=1.0,
    owner_space="WORLD",
    target_space="WORLD",
    stack_index=-1,
    subtarget=None,
):
    if constraint_type not in _TRACK_CONSTRAINTS:
        raise ValueError(f"Unsupported tracking constraint: {constraint_type}")
    _required_name(name, "constraint_name")
    influence = _finite_number(influence, "influence")
    if not 0 <= influence <= 1:
        raise ValueError("influence must be between 0 and 1")
    if subtarget:
        bones = getattr(getattr(target, "data", None), "bones", None)
        if target.type != "ARMATURE" or bones is None or bones.get(subtarget) is None:
            raise ValueError(f"Bone subtarget '{subtarget}' does not exist on armature target '{target.name}'")
    existing = camera.constraints.get(name)
    if existing is not None and existing.type != constraint_type:
        raise ValueError(f"Constraint '{name}' already exists with type {existing.type}; use a different stable name")
    constraint = existing or camera.constraints.new(type=constraint_type)
    created = existing is None
    if created:
        constraint.name = name
    old = {}
    fields = {
        "target": target,
        "subtarget": subtarget or "",
        "track_axis": track_axis,
        "influence": influence,
        "owner_space": owner_space,
        "target_space": target_space,
    }
    if constraint_type == "TRACK_TO":
        fields["up_axis"] = up_axis
    if constraint_type == "LOCKED_TRACK":
        fields["lock_axis"] = lock_axis
    fields = {field: value for field, value in fields.items() if hasattr(constraint, field)}
    try:
        for field in fields:
            old[field] = getattr(constraint, field)
        for field, value in fields.items():
            setattr(constraint, field, value)
        if stack_index >= 0:
            source = list(camera.constraints).index(constraint)
            destination = min(stack_index, len(camera.constraints) - 1)
            camera.constraints.move(source, destination)
    except Exception:
        if created:
            camera.constraints.remove(constraint)
        else:
            for field, value in old.items():
                setattr(constraint, field, value)
        raise
    return constraint


def _snapshot_constraint(owner, name):
    constraint = owner.constraints.get(name)
    if constraint is None:
        return None
    fields = {}
    for field in (
        "target",
        "subtarget",
        "track_axis",
        "up_axis",
        "lock_axis",
        "influence",
        "owner_space",
        "target_space",
    ):
        if hasattr(constraint, field):
            fields[field] = getattr(constraint, field)
    return {"type": constraint.type, "index": list(owner.constraints).index(constraint), "fields": fields}


def _restore_constraint(owner, name, snapshot):
    constraint = owner.constraints.get(name)
    if snapshot is None:
        if constraint is not None:
            owner.constraints.remove(constraint)
        return
    if constraint is None or constraint.type != snapshot["type"]:
        if constraint is not None:
            owner.constraints.remove(constraint)
        constraint = owner.constraints.new(type=snapshot["type"])
        constraint.name = name
    for field, value in snapshot["fields"].items():
        setattr(constraint, field, value)
    owner.constraints.move(list(owner.constraints).index(constraint), snapshot["index"])


def _new_empty(collection, name, location, rig_id, role, *, display_type="PLAIN_AXES", size=0.5):
    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    obj.location = location
    obj.empty_display_type = display_type
    obj.empty_display_size = size
    _tag(obj, rig_id, role)
    return obj


def _new_camera_object(collection, name, lens, rig_id=None, role="camera"):
    data = bpy.data.cameras.new(f"{name} Data")
    data.lens = _positive(lens, "lens")
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    if rig_id is not None:
        _tag(obj, rig_id, role)
        data["mcp_camera_rig_id"] = rig_id
        data["mcp_camera_role"] = "camera_data"
        data["mcp_camera_schema_version"] = _RIG_SCHEMA_VERSION
        data["mcp_camera_owner"] = "blender-mcp"
    return obj


def _parent_local(child, parent, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0)):
    child.parent = parent
    child.matrix_parent_inverse = mathutils.Matrix.Identity(4)
    child.location = location
    child.rotation_mode = "XYZ"
    child.rotation_euler = rotation


def _evaluated_bounds(objects):
    _update_view_layer()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        matrix = evaluated.matrix_world
        points.extend(matrix @ mathutils.Vector(corner) for corner in evaluated.bound_box)
    if not points:
        raise ValueError("The requested objects have no evaluable bounds")
    minimum = mathutils.Vector(tuple(min(point[index] for point in points) for index in range(3)))
    maximum = mathutils.Vector(tuple(max(point[index] for point in points) for index in range(3)))
    return points, minimum, maximum, (minimum + maximum) * 0.5


def _margin_limits(camera_data, scene, margin):
    frame = camera_data.view_frame(scene=scene)
    perspective = camera_data.type != "ORTHO"
    if perspective:
        xs = [point.x / -point.z for point in frame]
        ys = [point.y / -point.z for point in frame]
    else:
        xs = [point.x for point in frame]
        ys = [point.y for point in frame]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    inset = margin
    return (
        xmin + (xmax - xmin) * inset,
        xmax - (xmax - xmin) * inset,
        ymin + (ymax - ymin) * inset,
        ymax - (ymax - ymin) * inset,
    )


def _frame_contains(local_points, limits, *, perspective):
    xmin, xmax, ymin, ymax = limits
    x_epsilon = max((xmax - xmin) * 1e-6, 1e-9)
    y_epsilon = max((ymax - ymin) * 1e-6, 1e-9)
    for point in local_points:
        if perspective:
            depth = -point.z
            if depth <= 1e-8:
                return False
            x, y = point.x / depth, point.y / depth
        else:
            x, y = point.x, point.y
        if x < xmin - x_epsilon or x > xmax + x_epsilon or y < ymin - y_epsilon or y > ymax + y_epsilon:
            return False
    return True


def _limiting_axis(local_points, limits, *, perspective):
    xmin, xmax, ymin, ymax = limits
    x_center, y_center = (xmin + xmax) * 0.5, (ymin + ymax) * 0.5
    x_half, y_half = max((xmax - xmin) * 0.5, 1e-12), max((ymax - ymin) * 0.5, 1e-12)
    x_score = y_score = 0.0
    for point in local_points:
        depth = -point.z
        x = point.x / depth if perspective else point.x
        y = point.y / depth if perspective else point.y
        x_score = max(x_score, abs(x - x_center) / x_half)
        y_score = max(y_score, abs(y - y_center) / y_half)
    return "HORIZONTAL" if x_score >= y_score else "VERTICAL"


def _binary_smallest_fit(predicate, low, high):
    for _attempt in range(32):
        if predicate(high):
            break
        high *= 2
    else:
        raise ValueError("Unable to solve a framing distance or scale within a finite range")
    for _iteration in range(60):
        middle = (low + high) * 0.5
        if predicate(middle):
            high = middle
        else:
            low = middle
    return high


class CameraHandlersMixin:
    """Provide camera inspection, configuration, framing, and rig-construction handlers."""

    def get_camera_rig_info(
        self,
        scene_name,
        object_name,
        descendant_depth=4,
        child_limit=50,
        child_offset=0,
        animation_limit=100,
        animation_offset=0,
    ):
        scene = _scene(scene_name)
        root = _object(object_name, scene=scene)
        descendant_depth = _bounded_int(descendant_depth, "descendant_depth", 0, 12)
        child_limit = _bounded_int(child_limit, "child_limit", 1, 200)
        child_offset = _bounded_int(child_offset, "child_offset", 0, _MAX_RIG_DESCENDANTS - 1)
        animation_limit = _bounded_int(animation_limit, "animation_limit", 1, 500)
        animation_offset = _bounded_int(animation_offset, "animation_offset", 0, _MAX_ANIMATION_RECORDS - 1)
        descendants, descendants_capped = _descendants(root, descendant_depth)
        child_start, child_end, child_truncated, child_next = paginate(len(descendants), child_offset, child_limit, 200)
        members = []
        for obj, depth in descendants[child_start:child_end]:
            members.append(
                {
                    "name": obj.name,
                    "type": obj.type,
                    "depth": depth,
                    "parent": obj.parent.name if obj.parent else None,
                    "constraints": [_constraint_info(item) for item in obj.constraints],
                    "drivers": _driver_records(obj),
                    "rig_metadata": _rig_metadata(obj),
                }
            )
        animation = _action_records("OBJECT", root, _MAX_ANIMATION_RECORDS)
        if root.type == "CAMERA":
            animation.extend(_action_records("CAMERA_DATA", root.data, _MAX_ANIMATION_RECORDS - len(animation)))
        for descendant, _depth in descendants:
            remaining = _MAX_ANIMATION_RECORDS - len(animation)
            if remaining <= 0:
                break
            animation.extend(_action_records(descendant.name, descendant, remaining))
            if descendant.type == "CAMERA":
                remaining = _MAX_ANIMATION_RECORDS - len(animation)
                if remaining <= 0:
                    break
                animation.extend(_action_records(f"{descendant.name}:CAMERA_DATA", descendant.data, remaining))
        animation_capped = len(animation) >= _MAX_ANIMATION_RECORDS
        anim_start, anim_end, anim_truncated, anim_next = paginate(
            len(animation), animation_offset, animation_limit, 500
        )
        inspected_camera_names = {obj.name for obj, _depth in [(root, 0), *descendants] if obj.type == "CAMERA"}
        markers = [
            {"name": marker.name, "frame": marker.frame, "camera": marker.camera.name}
            for marker in scene.timeline_markers
            if marker.camera is not None and marker.camera.name in inspected_camera_names
        ]
        render = scene.render
        result = {
            "scene": scene.name,
            "object": root.name,
            "object_type": root.type,
            "parent_hierarchy": _parent_hierarchy(root),
            "transform": _transform_info(root),
            "constraints": [_constraint_info(item) for item in root.constraints],
            "drivers": _driver_records(root),
            "rig_metadata": _rig_metadata(root),
            "active_scene_camera": scene.camera == root,
            "camera_markers": markers,
            "render_gate": {
                "resolution_x": render.resolution_x,
                "resolution_y": render.resolution_y,
                "resolution_percentage": render.resolution_percentage,
                "pixel_aspect_x": render.pixel_aspect_x,
                "pixel_aspect_y": render.pixel_aspect_y,
                "display_aspect": (render.resolution_x * render.pixel_aspect_x)
                / (render.resolution_y * render.pixel_aspect_y),
            },
            "children": members,
            "children_total": len(descendants),
            "children_returned_count": len(members),
            "children_truncated": child_truncated or descendants_capped,
            "children_next_offset": child_next,
            "children_scan_capped": descendants_capped,
            "animation": animation[anim_start:anim_end],
            "animation_total": len(animation),
            "animation_returned_count": anim_end - anim_start,
            "animation_truncated": anim_truncated or animation_capped,
            "animation_next_offset": anim_next,
            "animation_scan_capped": animation_capped,
        }
        if root.type == "CAMERA":
            result["camera_data"] = root.data.name
            result["camera"] = _camera_settings(root.data)
        return result

    def create_camera(
        self,
        scene_name,
        collection_name,
        name,
        projection="PERSP",
        location=(0.0, 0.0, 0.0),
        rotation_euler=None,
        rotation_quaternion=None,
        look_at_object_name=None,
        look_at_point=None,
        optics=None,
        make_active=False,
    ):
        scene = _scene(scene_name)
        _required_name(name, "name")
        orientation_count = sum(
            value is not None for value in (rotation_euler, rotation_quaternion, look_at_object_name, look_at_point)
        )
        if orientation_count > 1:
            raise ValueError("Supply only one camera orientation source")
        world_location = _vector(location, "location")
        look_target = None
        if look_at_object_name is not None:
            _update_view_layer()
            look_target = _object(look_at_object_name, scene=scene).matrix_world.translation.copy()
        elif look_at_point is not None:
            look_target = _vector(look_at_point, "look_at_point")
        if rotation_euler is not None:
            rotation_euler = _vector(rotation_euler, "rotation_euler")
        if rotation_quaternion is not None:
            if len(rotation_quaternion) != 4:
                raise ValueError("rotation_quaternion must contain [w, x, y, z]")
            values = tuple(_finite_number(value, "rotation_quaternion") for value in rotation_quaternion)
            quaternion = mathutils.Quaternion(values)
            if quaternion.length_squared <= 1e-16:
                raise ValueError("rotation_quaternion must not be zero-length")
            quaternion.normalize()
        else:
            quaternion = None

        if optics and optics.get("projection") not in {None, projection}:
            raise ValueError("projection conflicts with optics.projection; supply projection in only one place")
        collection = _ensure_collection(scene, collection_name)
        data = bpy.data.cameras.new(f"{name} Data")
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        patch = {"projection": projection, **(optics or {})}
        validated = _validate_optics(data, patch)
        _patch_values(data, validated, _CAMERA_OPTICS | {"type"})
        obj.location = world_location
        if rotation_euler is not None:
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = rotation_euler
        elif quaternion is not None:
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = quaternion
        elif look_target is not None:
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = _look_quaternion(world_location, look_target)
        if make_active:
            scene.camera = obj
        return {
            "object": obj.name,
            "camera_data": data.name,
            "collection": collection.name,
            "scene": scene.name,
            "active_scene_camera": scene.camera == obj,
            "transform": _transform_info(obj),
            "settings": _camera_settings(data),
            "changed_objects": [obj.name],
            "changed_resources": [data.name],
        }

    def configure_camera(self, camera_name, optics=None, display=None):
        camera = _camera(camera_name)
        if not optics and not display:
            raise ValueError("Provide at least one optics or display field to change")
        optics_patch = _validate_optics(camera.data, optics)
        display_patch = _validate_display(display)
        old_optics, new_optics = _patch_values(camera.data, optics_patch, _CAMERA_OPTICS | {"type"})
        try:
            old_display, new_display = _patch_values(camera.data, display_patch, _CAMERA_DISPLAY)
        except Exception:
            for field, value in old_optics.items():
                setattr(camera.data, field, value)
            raise
        return {
            "camera": camera.name,
            "camera_data": camera.data.name,
            "old": {**old_optics, **old_display},
            "new": {**new_optics, **new_display},
            "changed_objects": [camera.name],
            "changed_resources": [camera.data.name],
        }

    def set_scene_camera(
        self,
        scene_name,
        camera_name,
        marker_name=None,
        marker_frame=None,
        replace_marker=False,
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene)
        if (marker_name is None) != (marker_frame is None):
            raise ValueError("marker_name and marker_frame must be supplied together")
        marker = None
        marker_created = False
        marker_old = None
        if marker_name is not None:
            assert marker_frame is not None
            if not marker_name.strip():
                raise ValueError("marker_name must be non-empty")
            marker_frame = _bounded_int(marker_frame, "marker_frame", _MIN_FRAME, _MAX_FRAME)
            by_name = scene.timeline_markers.get(marker_name)
            at_frame = [item for item in scene.timeline_markers if item.frame == marker_frame]
            marker = by_name or (at_frame[0] if at_frame else None)
            if marker is not None:
                conflicts = (
                    marker.name != marker_name
                    or marker.frame != marker_frame
                    or (marker.camera is not None and marker.camera is not camera)
                )
                if conflicts and not replace_marker:
                    raise ValueError(
                        f"Marker collision at name '{marker_name}' or frame {marker_frame}; "
                        "set replace_marker=true to replace"
                    )
                marker_old = (marker.name, marker.frame, marker.camera)
            else:
                marker = scene.timeline_markers.new(marker_name, frame=marker_frame)
                marker_created = True
        previous = scene.camera
        try:
            scene.camera = camera
            if marker is not None:
                marker.name = marker_name
                marker.frame = marker_frame
                marker.camera = camera
        except Exception:
            scene.camera = previous
            if marker_created:
                scene.timeline_markers.remove(marker)
            elif marker is not None and marker_old is not None:
                marker.name, marker.frame, marker.camera = marker_old
            raise
        return {
            "scene": scene.name,
            "previous_camera": previous.name if previous else None,
            "camera": camera.name,
            "marker": ({"name": marker.name, "frame": marker.frame, "camera": marker.camera.name} if marker else None),
            "changed_objects": [],
        }

    def aim_camera(
        self,
        scene_name,
        camera_name,
        mode="IMMEDIATE",
        target_object_name=None,
        target_point=None,
        subtarget=None,
        controls_collection_name="MCP Camera Controls",
        constraint_name="MCP Aim",
        constraint_type="DAMPED_TRACK",
        track_axis="TRACK_NEGATIVE_Z",
        up_axis="UP_Y",
        lock_axis="LOCK_Y",
        influence=1.0,
        owner_space="WORLD",
        target_space="WORLD",
        stack_index=-1,
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene)
        if (target_object_name is None) == (target_point is None):
            raise ValueError("Supply exactly one of target_object_name or target_point")
        if subtarget and target_object_name is None:
            raise ValueError("subtarget requires target_object_name")
        target = _object(target_object_name, scene=scene) if target_object_name is not None else None
        point = _target_world_point(target, subtarget) if target is not None else _vector(target_point, "target_point")
        mode = str(mode).upper()
        if mode == "IMMEDIATE":
            _set_world_rotation(camera, _look_quaternion(camera.matrix_world.translation, point))
            return {
                "camera": camera.name,
                "mode": mode,
                "target_object": target.name if target else None,
                "target_point": list(point),
                "transform": _transform_info(camera),
                "changed_objects": [camera.name],
            }
        if mode != "CONSTRAINT":
            raise ValueError("mode must be IMMEDIATE or CONSTRAINT")
        created_target = None
        if target is None:
            collection = _ensure_collection(scene, controls_collection_name)
            rig_id = str(uuid.uuid4())
            target = _new_empty(collection, f"{camera.name} Aim", point, rig_id, "target", display_type="SPHERE")
            created_target = target
        constraint = _add_constraint(
            camera,
            target,
            name=constraint_name,
            constraint_type=constraint_type,
            track_axis=track_axis,
            up_axis=up_axis,
            lock_axis=lock_axis,
            influence=influence,
            owner_space=owner_space,
            target_space=target_space,
            stack_index=stack_index,
            subtarget=subtarget,
        )
        changed = [camera.name]
        if created_target:
            changed.append(created_target.name)
        return {
            "camera": camera.name,
            "mode": mode,
            "target_object": target.name,
            "constraint": _constraint_info(constraint),
            "constraint_index": list(camera.constraints).index(constraint),
            "retained_dependencies": [target.name],
            "changed_objects": changed,
        }

    def create_camera_target(
        self,
        scene_name,
        collection_name,
        name,
        location=None,
        target_object_name=None,
        use_evaluated_bounds_center=True,
        reuse=False,
        camera_names=None,
        constraint_type="DAMPED_TRACK",
    ):
        scene = _scene(scene_name)
        _required_name(name, "name")
        if (location is None) == (target_object_name is None):
            raise ValueError("Supply exactly one of location or target_object_name")
        source = _object(target_object_name, scene=scene) if target_object_name is not None else None
        camera_names = camera_names or []
        if len(set(camera_names)) != len(camera_names):
            raise ValueError("camera_names must not contain duplicates")
        cameras = [_camera(camera_name, scene=scene) for camera_name in camera_names]
        if constraint_type not in _TRACK_CONSTRAINTS:
            raise ValueError(f"Unsupported tracking constraint: {constraint_type}")
        for camera in cameras:
            existing_constraint = camera.constraints.get(f"MCP Aim: {name}")
            if existing_constraint is not None and existing_constraint.type != constraint_type:
                raise ValueError(f"Constraint 'MCP Aim: {name}' on '{camera.name}' has type {existing_constraint.type}")
        if source is not None and use_evaluated_bounds_center:
            _points, _minimum, _maximum, target_location = _evaluated_bounds([source])
        elif source is not None:
            _update_view_layer()
            target_location = source.matrix_world.translation.copy()
        else:
            target_location = _vector(location, "location")
        existing = bpy.data.objects.get(name)
        target_matrix = existing.matrix_world.copy() if existing is not None else None
        constraint_name = f"MCP Aim: {name}"
        constraint_snapshots = [(camera, _snapshot_constraint(camera, constraint_name)) for camera in cameras]
        created = False
        if existing is not None:
            if not reuse:
                raise ValueError(f"Object '{name}' already exists; set reuse=true only for a tagged target")
            if existing.type != "EMPTY" or existing.get("mcp_camera_role") != "target":
                raise ValueError(f"Object '{name}' is not an Empty tagged as an MCP camera target")
            if existing.name not in scene.objects:
                raise ValueError(f"Existing target '{name}' is not linked to scene '{scene.name}'")
            target = existing
        else:
            collection = _ensure_collection(scene, collection_name)
            target = _new_empty(
                collection,
                name,
                target_location,
                str(uuid.uuid4()),
                "target",
                display_type="SPHERE",
            )
            created = True
        constraints = []
        try:
            target.matrix_world.translation = target_location
            for camera in cameras:
                constraint = _add_constraint(
                    camera,
                    target,
                    name=constraint_name,
                    constraint_type=constraint_type,
                )
                constraints.append({"camera": camera.name, **_constraint_info(constraint)})
        except Exception:
            for camera, snapshot in constraint_snapshots:
                _restore_constraint(camera, constraint_name, snapshot)
            if created:
                bpy.data.objects.remove(target, do_unlink=True)  # pyright: ignore[reportArgumentType]
            elif target_matrix is not None:
                target.matrix_world = target_matrix
            raise
        changed = [target.name, *(camera.name for camera in cameras)]
        return {
            "target": target.name,
            "created": created,
            "location_world": list(target.matrix_world.translation),
            "source_object": source.name if source else None,
            "constraints": constraints,
            "changed_objects": changed,
        }

    def frame_camera_on_objects(
        self,
        scene_name,
        camera_name,
        object_names,
        margin=0.1,
        policy="MOVE_CAMERA",
        aim_at_center=True,
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene)
        if not object_names:
            raise ValueError("object_names must not be empty")
        if len(set(object_names)) != len(object_names):
            raise ValueError("object_names must not contain duplicates")
        objects = [_object(name, scene=scene) for name in object_names]
        margin = _finite_number(margin, "margin")
        if not 0 <= margin < 0.9:
            raise ValueError("margin must be in [0, 0.9)")
        policy = str(policy).upper()
        policy_types = {
            "MOVE_CAMERA": "PERSP",
            "CHANGE_LENS": "PERSP",
            "CHANGE_ORTHO_SCALE": "ORTHO",
        }
        if policy not in policy_types:
            raise ValueError(f"Unsupported framing policy: {policy}")
        required_type = policy_types[policy]
        if camera.data.type != required_type:
            raise ValueError(f"{policy} framing requires a {required_type} camera")
        points, minimum, maximum, center = _evaluated_bounds(objects)
        original_matrix = camera.matrix_world.copy()
        original_lens = camera.data.lens
        original_ortho_scale = camera.data.ortho_scale
        location, current_rotation, scale = original_matrix.decompose()
        rotation = _look_quaternion(location, center) if aim_at_center else current_rotation
        result = {}
        try:
            if policy == "MOVE_CAMERA":
                inverse_rotation = rotation.conjugated()
                centered = [inverse_rotation @ (point - center) for point in points]
                limits = _margin_limits(camera.data, scene, margin)

                def distance_fits(distance):
                    local = [mathutils.Vector((point.x, point.y, point.z - distance)) for point in centered]
                    return _frame_contains(local, limits, perspective=True)

                initial_high = max((maximum - minimum).length, camera.data.clip_start * 2, 1.0)
                distance = _binary_smallest_fit(distance_fits, camera.data.clip_start, initial_high) * 1.00001
                solved_location = center + (rotation @ mathutils.Vector((0.0, 0.0, distance)))
                camera.matrix_world = mathutils.Matrix.LocRotScale(solved_location, rotation, scale)
                result = {"distance": distance, "lens": camera.data.lens}
            elif policy == "CHANGE_LENS":
                inverse_rotation = rotation.conjugated()
                local_points = [inverse_rotation @ (point - location) for point in points]
                if any(point.z >= -1e-8 for point in local_points):
                    raise ValueError("Cannot change lens because at least one bound is on or behind the camera plane")
                minimum_lens = max(float(camera.data.bl_rna.properties["lens"].hard_min), 0.01)
                camera.data.lens = minimum_lens
                if not _frame_contains(local_points, _margin_limits(camera.data, scene, margin), perspective=True):
                    raise ValueError("Objects do not fit even at the camera's minimum supported lens")
                low, high = minimum_lens, float(camera.data.bl_rna.properties["lens"].hard_max)
                for _iteration in range(60):
                    middle = (low + high) * 0.5
                    camera.data.lens = middle
                    if _frame_contains(local_points, _margin_limits(camera.data, scene, margin), perspective=True):
                        low = middle
                    else:
                        high = middle
                low = max(minimum_lens, low * 0.99999)
                camera.data.lens = low
                _set_world_rotation(camera, rotation)
                result = {"distance": -sum(point.z for point in local_points) / len(local_points), "lens": low}
            else:
                inverse_rotation = rotation.conjugated()
                local_points = [inverse_rotation @ (point - location) for point in points]

                def scale_fits(scale_value):
                    camera.data.ortho_scale = scale_value
                    return _frame_contains(local_points, _margin_limits(camera.data, scene, margin), perspective=False)

                ortho_scale = (
                    _binary_smallest_fit(
                        scale_fits,
                        1e-6,
                        max(original_ortho_scale, (maximum - minimum).length, 1.0),
                    )
                    * 1.00001
                )
                camera.data.ortho_scale = ortho_scale
                _set_world_rotation(camera, rotation)
                result = {"ortho_scale": ortho_scale}
            _update_view_layer()
            limits = _margin_limits(camera.data, scene, margin)
            local_points = [camera.matrix_world.inverted() @ point for point in points]
            if not _frame_contains(local_points, limits, perspective=camera.data.type == "PERSP"):
                raise ValueError(
                    "The assigned camera state does not frame the objects; active constraints may be overriding it"
                )
        except Exception:
            camera.matrix_world = original_matrix
            camera.data.lens = original_lens
            camera.data.ortho_scale = original_ortho_scale
            raise
        limiting_axis = _limiting_axis(local_points, limits, perspective=camera.data.type == "PERSP")
        return {
            "camera": camera.name,
            "objects": [obj.name for obj in objects],
            "policy": policy,
            "margin": margin,
            "bounds_world": {"min": list(minimum), "max": list(maximum)},
            "target_point_world": list(center),
            "limiting_axis": limiting_axis,
            "transform": _transform_info(camera),
            **result,
            "changed_objects": [camera.name],
            "changed_resources": [camera.data.name] if policy != "MOVE_CAMERA" else [],
        }

    def create_orbit_camera_rig(
        self,
        scene_name,
        collection_name,
        rig_name,
        pivot,
        radius,
        azimuth=0.0,
        elevation=0.0,
        roll=0.0,
        lens=50.0,
        target_height=0.0,
    ):
        scene = _scene(scene_name)
        _required_name(rig_name, "rig_name")
        pivot = _vector(pivot, "pivot")
        radius = _positive(radius, "radius")
        azimuth = _finite_number(azimuth, "azimuth")
        elevation = _finite_number(elevation, "elevation")
        roll = _finite_number(roll, "roll")
        target_height = _finite_number(target_height, "target_height")
        _positive(lens, "lens")
        collection = _ensure_collection(scene, collection_name)
        rig_id = str(uuid.uuid4())
        root = _new_empty(collection, f"{rig_name} Root", pivot, rig_id, "root", display_type="CIRCLE")
        root.rotation_euler.z = azimuth
        boom = _new_empty(collection, f"{rig_name} Boom", (0.0, 0.0, 0.0), rig_id, "boom", display_type="CUBE")
        horizontal = radius * math.cos(elevation)
        _parent_local(boom, root, (horizontal, 0.0, radius * math.sin(elevation)))
        camera = _new_camera_object(collection, f"{rig_name} Camera", lens, rig_id)
        _parent_local(camera, boom, rotation=(0.0, 0.0, roll))
        target = _new_empty(
            collection,
            f"{rig_name} Target",
            (0.0, 0.0, target_height),
            rig_id,
            "target",
            display_type="SPHERE",
        )
        _parent_local(target, root, (0.0, 0.0, target_height))
        constraint = _add_constraint(camera, target, name="MCP Orbit Aim", constraint_type="DAMPED_TRACK")
        root["mcp_camera_azimuth"] = azimuth
        boom["mcp_camera_radius"] = radius
        boom["mcp_camera_elevation"] = elevation
        return self._rig_result("ORBIT", rig_id, collection, root, camera, [root, boom, target, camera], [constraint])

    def create_dolly_camera_rig(
        self,
        scene_name,
        collection_name,
        rig_name,
        location,
        rail_direction=(0.0, 1.0, 0.0),
        yaw=0.0,
        camera_height=1.5,
        target_distance=10.0,
        lens=50.0,
        create_target=True,
    ):
        scene = _scene(scene_name)
        _required_name(rig_name, "rig_name")
        location = _vector(location, "location")
        rail = _vector(rail_direction, "rail_direction")
        if rail.length_squared <= 1e-16:
            raise ValueError("rail_direction must be non-zero")
        rail.normalize()
        camera_height = _positive(camera_height, "camera_height", allow_zero=True)
        target_distance = _positive(target_distance, "target_distance")
        _positive(lens, "lens")
        collection = _ensure_collection(scene, collection_name)
        rig_id = str(uuid.uuid4())
        root = _new_empty(collection, f"{rig_name} Root", location, rig_id, "root", display_type="ARROWS")
        root.rotation_euler.z = _finite_number(yaw, "yaw")
        root["mcp_camera_rail_direction"] = list(rail)
        control = _new_empty(
            collection, f"{rig_name} Camera Control", (0.0, 0.0, 0.0), rig_id, "camera_control", display_type="CUBE"
        )
        _parent_local(control, root, (0.0, 0.0, camera_height))
        camera = _new_camera_object(collection, f"{rig_name} Camera", lens, rig_id)
        _parent_local(camera, control)
        members = [root, control, camera]
        constraints = []
        if create_target:
            world_rail = mathutils.Matrix.Rotation(float(root.rotation_euler.z), 4, "Z") @ rail
            target = _new_empty(
                collection,
                f"{rig_name} Target",
                location + world_rail * target_distance + mathutils.Vector((0.0, 0.0, camera_height)),
                rig_id,
                "target",
                display_type="SPHERE",
            )
            constraints.append(_add_constraint(camera, target, name="MCP Dolly Aim", constraint_type="DAMPED_TRACK"))
            members.append(target)
        return self._rig_result("DOLLY", rig_id, collection, root, camera, members, constraints)

    def create_crane_camera_rig(
        self,
        scene_name,
        collection_name,
        rig_name,
        location,
        base_height=1.0,
        arm_length=5.0,
        elevation=0.0,
        pan=0.0,
        tilt=0.0,
        roll=0.0,
        lens=50.0,
        create_target=True,
    ):
        scene = _scene(scene_name)
        _required_name(rig_name, "rig_name")
        location = _vector(location, "location")
        base_height = _positive(base_height, "base_height", allow_zero=True)
        arm_length = _positive(arm_length, "arm_length")
        angles = {
            key: _finite_number(value, key)
            for key, value in {"elevation": elevation, "pan": pan, "tilt": tilt, "roll": roll}.items()
        }
        _positive(lens, "lens")
        collection = _ensure_collection(scene, collection_name)
        rig_id = str(uuid.uuid4())
        root = _new_empty(collection, f"{rig_name} Base", location, rig_id, "root", display_type="CIRCLE")
        root.rotation_euler.z = angles["pan"]
        pivot = _new_empty(collection, f"{rig_name} Arm Pivot", (0.0, 0.0, 0.0), rig_id, "arm_pivot")
        _parent_local(pivot, root, (0.0, 0.0, base_height), (0.0, -angles["elevation"], 0.0))
        boom = _new_empty(collection, f"{rig_name} Boom", (0.0, 0.0, 0.0), rig_id, "boom", display_type="SINGLE_ARROW")
        _parent_local(boom, pivot, (arm_length, 0.0, 0.0))
        boom["mcp_camera_arm_length"] = arm_length
        head = _new_empty(collection, f"{rig_name} Head", (0.0, 0.0, 0.0), rig_id, "head", display_type="CUBE")
        _parent_local(head, boom, rotation=(angles["tilt"], 0.0, angles["roll"]))
        camera = _new_camera_object(collection, f"{rig_name} Camera", lens, rig_id)
        _parent_local(camera, head)
        members = [root, pivot, boom, head, camera]
        constraints = []
        if create_target:
            target = _new_empty(
                collection,
                f"{rig_name} Target",
                location,
                rig_id,
                "target",
                display_type="SPHERE",
            )
            constraints.append(_add_constraint(camera, target, name="MCP Crane Aim", constraint_type="DAMPED_TRACK"))
            members.append(target)
        return self._rig_result("CRANE", rig_id, collection, root, camera, members, constraints)

    def create_camera_path_rig(
        self,
        scene_name,
        collection_name,
        rig_name,
        camera_name,
        curve_object_name=None,
        path_points=None,
        spline_type="BEZIER",
        forward_axis="TRACK_NEGATIVE_Z",
        up_axis="UP_Y",
        use_curve_follow=True,
        start_frame=None,
        end_frame=None,
        target_object_name=None,
    ):
        scene = _scene(scene_name)
        _required_name(rig_name, "rig_name")
        camera = _camera(camera_name, scene=scene)
        original_camera_world = camera.matrix_world.copy()
        if (curve_object_name is None) == (path_points is None):
            raise ValueError("Supply exactly one of curve_object_name or path_points")
        if (start_frame is None) != (end_frame is None):
            raise ValueError("start_frame and end_frame must be supplied together")
        animation_start = animation_end = None
        if start_frame is not None:
            assert end_frame is not None
            animation_start = _bounded_int(start_frame, "start_frame", _MIN_FRAME, _MAX_FRAME)
            animation_end = _bounded_int(end_frame, "end_frame", _MIN_FRAME, _MAX_FRAME)
            if animation_start >= animation_end:
                raise ValueError("start_frame must be less than end_frame")
        target = _object(target_object_name, scene=scene) if target_object_name else None
        rig_id = str(uuid.uuid4())
        if curve_object_name is not None:
            curve = _object(curve_object_name, scene=scene)
            if curve.type != "CURVE":
                raise ValueError(f"Path object '{curve.name}' is not a curve (type={curve.type})")
            created_curve = False
        else:
            assert path_points is not None
            validated_points = [_vector(point, f"path_points[{index}]") for index, point in enumerate(path_points)]
            if len(validated_points) < 2:
                raise ValueError("path_points must contain at least two points")
            spline_type = str(spline_type).upper()
            if spline_type not in {"BEZIER", "NURBS"}:
                raise ValueError("spline_type must be BEZIER or NURBS")
            collection = _ensure_collection(scene, collection_name)
            curve_data = bpy.data.curves.new(f"{rig_name} Path Data", type="CURVE")
            curve_data.dimensions = "3D"
            spline = curve_data.splines.new("BEZIER" if spline_type == "BEZIER" else "NURBS")
            if spline_type == "BEZIER":
                spline.bezier_points.add(len(validated_points) - 1)
                for point, coordinate in zip(spline.bezier_points, validated_points, strict=True):
                    point.co = coordinate
                    point.handle_left_type = "AUTO"
                    point.handle_right_type = "AUTO"
            else:
                spline.points.add(len(validated_points) - 1)
                for point, coordinate in zip(spline.points, validated_points, strict=True):
                    point.co = (*coordinate, 1.0)
                spline.order_u = min(4, len(validated_points))
                spline.use_endpoint_u = True
            curve = bpy.data.objects.new(f"{rig_name} Path", curve_data)
            collection.objects.link(curve)
            _tag(curve, rig_id, "path")
            created_curve = True
        if curve_object_name is not None:
            collection = _ensure_collection(scene, collection_name)
        root = _new_empty(collection, f"{rig_name} Root", (0.0, 0.0, 0.0), rig_id, "root", display_type="ARROWS")
        constraint = root.constraints.new(type="FOLLOW_PATH")
        constraint.name = "MCP Camera Path"
        constraint.target = curve
        constraint.forward_axis = forward_axis
        constraint.up_axis = up_axis
        constraint.use_curve_follow = bool(use_curve_follow)
        constraint.use_fixed_location = True
        if animation_start is not None:
            assert animation_end is not None
            original_offset = constraint.offset_factor
            constraint.offset_factor = 0.0
            constraint.keyframe_insert(data_path="offset_factor", frame=animation_start)
            constraint.offset_factor = 1.0
            constraint.keyframe_insert(data_path="offset_factor", frame=animation_end)
            constraint.offset_factor = original_offset
        constraints = [constraint]
        if target is not None:
            constraints.append(_add_constraint(camera, target, name="MCP Path Aim", constraint_type="DAMPED_TRACK"))
        _update_view_layer()
        camera.parent = root
        camera.matrix_parent_inverse = root.matrix_world.inverted()
        camera.matrix_world = original_camera_world
        _tag(camera, rig_id, "camera")
        members = [root, camera]
        if created_curve:
            members.append(curve)
        result = self._rig_result("PATH", rig_id, collection, root, camera, members, constraints)
        result["path"] = curve.name
        result["path_created"] = created_curve
        result["animation"] = (
            {"property": "offset_factor", "start_frame": animation_start, "end_frame": animation_end}
            if animation_start is not None
            else None
        )
        changed_resources = [curve.data.name] if created_curve else []
        root_action = getattr(getattr(root, "animation_data", None), "action", None)
        if root_action is not None:
            changed_resources.append(root_action.name)
        result["changed_resources"] = changed_resources
        return result

    def configure_camera_dof(
        self,
        scene_name,
        camera_name,
        patch,
        focus_object_name=None,
        focus_distance=None,
        focus_point=None,
        focus_target_name=None,
        focus_collection_name="MCP Camera Controls",
        reuse_focus_target=False,
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene)
        if sum(value is not None for value in (focus_object_name, focus_distance, focus_point)) > 1:
            raise ValueError("Supply at most one focus intent")
        focus_object = _object(focus_object_name, scene=scene) if focus_object_name else None
        if focus_distance is not None:
            focus_distance = _positive(focus_distance, "focus_distance")
        point = _vector(focus_point, "focus_point") if focus_point is not None else None
        if point is not None and not focus_target_name:
            raise ValueError("focus_target_name is required for a focus point")
        dof = camera.data.dof
        patch = patch or {}
        if not patch and focus_object_name is None and focus_distance is None and focus_point is None:
            raise ValueError("Provide at least one depth-of-field or focus change")
        for field in ("aperture_fstop", "aperture_ratio"):
            if field in patch:
                _positive(patch[field], field)
        if "aperture_blades" in patch:
            _bounded_int(patch["aperture_blades"], "aperture_blades", 0, 16)
        if "aperture_rotation" in patch:
            _finite_number(patch["aperture_rotation"], "aperture_rotation")
        existing_target = bpy.data.objects.get(focus_target_name) if focus_target_name else None
        existing_target_matrix = existing_target.matrix_world.copy() if existing_target is not None else None
        if existing_target is not None:
            if not reuse_focus_target:
                raise ValueError(f"Focus target '{focus_target_name}' exists; set reuse_focus_target=true")
            if existing_target.type != "EMPTY" or existing_target.get("mcp_camera_role") != "focus_target":
                raise ValueError(f"Object '{focus_target_name}' is not a tagged MCP focus target")
        old_patch = {field: getattr(dof, field) for field in patch}
        old_focus_object = dof.focus_object
        old_focus_distance = dof.focus_distance
        created_target = None
        try:
            old, new = _patch_values(dof, patch, _DOF_FIELDS)
            if point is not None:
                if existing_target is not None:
                    existing_target.matrix_world.translation = point
                    focus_object = existing_target
                else:
                    collection = _ensure_collection(scene, focus_collection_name)
                    created_target = _new_empty(
                        collection,
                        focus_target_name,
                        point,
                        str(uuid.uuid4()),
                        "focus_target",
                        display_type="SPHERE",
                    )
                    focus_object = created_target
            if focus_object is not None:
                dof.focus_object = focus_object
            elif focus_distance is not None:
                dof.focus_object = None
                dof.focus_distance = focus_distance
        except Exception:
            for field, value in old_patch.items():
                setattr(dof, field, value)
            dof.focus_object = old_focus_object
            dof.focus_distance = old_focus_distance
            if existing_target is not None and existing_target_matrix is not None:
                existing_target.matrix_world = existing_target_matrix
            if created_target is not None:
                bpy.data.objects.remove(created_target, do_unlink=True)
            raise
        changed = [camera.name]
        if created_target:
            changed.append(created_target.name)
        return {
            "camera": camera.name,
            "camera_data": camera.data.name,
            "old": {
                **old,
                "focus_object": getattr(old_focus_object, "name", None),
                "focus_distance": old_focus_distance,
            },
            "new": {
                **new,
                "focus_object": getattr(dof.focus_object, "name", None),
                "focus_distance": dof.focus_distance,
            },
            "focus_intent": "OBJECT" if dof.focus_object else "DISTANCE",
            "changed_objects": changed,
            "changed_resources": [camera.data.name],
            "warnings": ["Depth-of-field appearance depends on the render engine and sampling settings."],
        }

    @staticmethod
    def _rig_result(rig_type, rig_id, collection, root, camera, members, constraints):
        _update_view_layer()
        return {
            "rig_type": rig_type,
            "rig_id": rig_id,
            "schema_version": _RIG_SCHEMA_VERSION,
            "collection": collection.name,
            "root": root.name,
            "camera": camera.name,
            "camera_data": camera.data.name,
            "members": [{"name": obj.name, "role": obj.get("mcp_camera_role"), "type": obj.type} for obj in members],
            "constraints": [_constraint_info(constraint) for constraint in constraints],
            "retained_live_dependencies": [
                getattr(constraint.target, "name", None)
                for constraint in constraints
                if getattr(constraint, "target", None) is not None
            ],
            "changed_objects": [obj.name for obj in members],
            "changed_resources": [camera.data.name],
        }
