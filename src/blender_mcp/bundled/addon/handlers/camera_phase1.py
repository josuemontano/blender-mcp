"""Blender-main-thread handlers for Phase 1 camera production workflows."""

import math
import uuid

import bpy
import mathutils

from .camera import (
    _CAMERA_OPTICS,
    _MAX_FRAME,
    _MIN_FRAME,
    _camera,
    _constraint_info,
    _ensure_collection,
    _finite_number,
    _matrix_list,
    _object,
    _patch_values,
    _plain,
    _required_name,
    _scene,
    _tag,
    _transform_info,
    _update_view_layer,
    _validate_display,
    _vector,
)

_OBJECT_CHANNELS = {
    "location": 3,
    "rotation_euler": 3,
    "rotation_quaternion": 4,
    "scale": 3,
}
_CAMERA_CHANNELS = {
    "lens",
    "ortho_scale",
    "shift_x",
    "shift_y",
    "clip_start",
    "clip_end",
}
_DOF_CHANNELS = {"focus_distance", "aperture_fstop"}
_CONSTRAINT_CHANNELS = {"influence", "offset_factor"}
_CAMERA_GUIDES = {
    "show_safe_areas",
    "show_composition_center",
    "show_composition_center_diagonal",
    "show_composition_golden",
    "show_composition_golden_tria_a",
    "show_composition_golden_tria_b",
    "show_composition_harmony_tri_a",
    "show_composition_harmony_tri_b",
    "show_composition_thirds",
}
_INTERPOLATIONS = {"CONSTANT", "LINEAR", "BEZIER"}
_HANDLE_TYPES = {"FREE", "ALIGNED", "VECTOR", "AUTO", "AUTO_CLAMPED"}
_CONSTRAINT_TYPES = {
    "TRACK_TO",
    "DAMPED_TRACK",
    "LOCKED_TRACK",
    "FOLLOW_PATH",
    "CHILD_OF",
    "COPY_LOCATION",
    "COPY_ROTATION",
    "COPY_TRANSFORMS",
    "LIMIT_LOCATION",
    "LIMIT_ROTATION",
    "LIMIT_SCALE",
}
_TARGETED_CONSTRAINTS = _CONSTRAINT_TYPES - {"LIMIT_LOCATION", "LIMIT_ROTATION", "LIMIT_SCALE"}
_COPY_CONSTRAINTS = {"COPY_LOCATION", "COPY_ROTATION"}
_OPTICS_COPY_FIELDS = _CAMERA_OPTICS - {"panorama_type"}
_MAX_RIG_MEMBERS = 2_000


def _frame(value, label):
    if isinstance(value, bool) or int(value) != value or not _MIN_FRAME <= int(value) <= _MAX_FRAME:
        raise ValueError(f"{label} must be an integer in [{_MIN_FRAME}, {_MAX_FRAME}]")
    return int(value)


def _animation_owner(record):
    obj = _object(record["object_name"])
    owner_kind = record.get("owner", "OBJECT")
    data_path = record["data_path"]
    constraint = None
    if owner_kind == "OBJECT":
        expected = _OBJECT_CHANNELS.get(data_path)
        if expected is None:
            raise ValueError(f"Unsupported object animation path: {data_path}")
        owner = obj
        fcurve_path = data_path
    elif owner_kind == "CAMERA_DATA":
        if obj.type != "CAMERA" or data_path not in _CAMERA_CHANNELS:
            raise ValueError(f"Unsupported camera-data animation path: {data_path}")
        owner = obj.data
        expected = 1
        fcurve_path = data_path
    elif owner_kind == "DOF":
        if obj.type != "CAMERA" or data_path not in _DOF_CHANNELS:
            raise ValueError(f"Unsupported depth-of-field animation path: {data_path}")
        owner = obj.data.dof
        expected = 1
        fcurve_path = owner.path_from_id(data_path)
    elif owner_kind == "CONSTRAINT":
        constraint_name = _required_name(record.get("constraint_name"), "constraint_name")
        constraint = obj.constraints.get(constraint_name)
        if constraint is None:
            raise ValueError(f"Constraint not found on '{obj.name}': {constraint_name}")
        if data_path not in _CONSTRAINT_CHANNELS or not hasattr(constraint, data_path):
            raise ValueError(f"Unsupported constraint animation path: {data_path}")
        owner = constraint
        expected = 1
        fcurve_path = constraint.path_from_id(data_path)
    else:
        raise ValueError(f"Unsupported animation owner: {owner_kind}")
    return obj, owner, expected, fcurve_path, constraint


def _normalized_key_value(value, expected, array_index, label):
    if isinstance(value, (list, tuple)):
        values = tuple(_finite_number(item, label) for item in value)
        if array_index is not None:
            raise ValueError(f"{label}: array_index requires a scalar value")
        if len(values) != expected:
            raise ValueError(f"{label}: expected {expected} values")
        return values
    scalar = _finite_number(value, label)
    if expected > 1 and array_index is None:
        raise ValueError(f"{label}: vector channels require a full vector or array_index")
    if array_index is not None and array_index >= expected:
        raise ValueError(f"{label}: array_index is outside the channel")
    return scalar


def _action_fcurves(id_owner):
    animation = getattr(id_owner, "animation_data", None)
    action = getattr(animation, "action", None) if animation is not None else None
    if action is None:
        return None, ()
    if hasattr(action, "fcurves"):
        return action, action.fcurves
    slot = getattr(animation, "action_slot", None)
    curves = []
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            if getattr(strip, "type", None) != "KEYFRAME":
                continue
            channelbag = strip.channelbag(slot, ensure=False) if slot is not None else None
            if channelbag is not None:
                curves.extend(channelbag.fcurves)
    return action, curves


def _find_fcurve(id_owner, data_path, array_index):
    _action, curves = _action_fcurves(id_owner)
    for curve in curves:
        if curve.data_path == data_path and (array_index is None or curve.array_index == array_index):
            return curve
    return None


def _key_at(curve, frame):
    if curve is None:
        return None
    return next((point for point in curve.keyframe_points if abs(point.co[0] - frame) <= 1e-5), None)


def _set_key_style(id_owner, data_path, frame, array_indices, interpolation, handle_left, handle_right):
    changed = []
    for index in array_indices:
        curve = _find_fcurve(id_owner, data_path, index)
        point = _key_at(curve, frame)
        if point is None:
            continue
        point.interpolation = interpolation
        if interpolation == "BEZIER":
            point.handle_left_type = handle_left
            point.handle_right_type = handle_right
        changed.append({"data_path": data_path, "array_index": index, "frame": frame})
    return changed


def _assign_and_key(record, resolved, interpolation, handle_left, handle_right):
    obj, owner, expected, fcurve_path, constraint = resolved
    data_path = record["data_path"]
    array_index = record.get("array_index")
    value = record["_value"]
    frame = record["frame"]
    old = getattr(owner, data_path)
    if array_index is None:
        setattr(owner, data_path, value)
    else:
        old = old[array_index]
        getattr(owner, data_path)[array_index] = value
    kwargs = {"data_path": data_path, "frame": frame}
    if array_index is not None:
        kwargs["index"] = array_index
    if not owner.keyframe_insert(**kwargs):
        if array_index is None:
            setattr(owner, data_path, old)
        else:
            getattr(owner, data_path)[array_index] = old
        raise RuntimeError(f"Blender refused keyframe insertion for {obj.name}:{fcurve_path} at {frame}")
    id_owner = obj if constraint is not None else (obj.data if record["owner"] in {"CAMERA_DATA", "DOF"} else obj)
    indices = [array_index] if array_index is not None else list(range(expected))
    styled = _set_key_style(id_owner, fcurve_path, frame, indices, interpolation, handle_left, handle_right)
    return styled


def _validate_key_style(interpolation, handle_left, handle_right):
    if interpolation not in _INTERPOLATIONS:
        raise ValueError(f"Unsupported interpolation: {interpolation}")
    if handle_left not in _HANDLE_TYPES or handle_right not in _HANDLE_TYPES:
        raise ValueError("Unsupported Bézier handle type")


def _subject_point(scene, object_name, point):
    if object_name is None:
        return _vector(point, "subject_point")
    target = _object(object_name, scene=scene)
    evaluated = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return evaluated.matrix_world.translation.copy()


def _focus_depth(camera, point):
    local = camera.matrix_world.inverted_safe() @ point
    return -float(local.z)


def _set_interpolation_on_keys(id_owner, data_path, frames, interpolation):
    action, curves = _action_fcurves(id_owner)
    if action is None:
        return []
    changed = []
    for curve in curves:
        if curve.data_path != data_path:
            continue
        for point in curve.keyframe_points:
            if any(abs(point.co[0] - frame) <= 1e-5 for frame in frames):
                point.interpolation = interpolation
                if interpolation == "BEZIER":
                    point.handle_left_type = "AUTO_CLAMPED"
                    point.handle_right_type = "AUTO_CLAMPED"
                changed.append({"data_path": data_path, "array_index": curve.array_index, "frame": point.co[0]})
    return changed


def _camera_cut_map(scene):
    return [
        {"name": marker.name, "frame": marker.frame, "camera": getattr(marker.camera, "name", None)}
        for marker in sorted(scene.timeline_markers, key=lambda item: (item.frame, item.name))
        if marker.camera is not None
    ]


def _copy_action(owner, policy):
    animation = getattr(owner, "animation_data", None)
    if animation is None or animation.action is None:
        return None
    if policy == "NONE":
        animation.action = None
    elif policy == "COPY":
        animation.action = animation.action.copy()
    return getattr(animation.action, "name", None)


def _matrix_close(first, second, tolerance=1e-5):
    return all(abs(first[row][column] - second[row][column]) <= tolerance for row in range(4) for column in range(4))


def _finding(severity, code, obj, prop, message, remediation, frame=None):
    result = {
        "severity": severity,
        "code": code,
        "object": getattr(obj, "name", obj),
        "property": prop,
        "message": message,
        "remediation": remediation,
    }
    if frame is not None:
        result["frame"] = frame
    return result


class CameraPhaseOneHandlersMixin:
    """Shot, animation, reusable-rig, and validation command handlers."""

    def keyframe_camera_rig(
        self,
        keyframes,
        policy="REPLACE",
        interpolation="BEZIER",
        handle_left="AUTO_CLAMPED",
        handle_right="AUTO_CLAMPED",
    ):
        if not isinstance(keyframes, list) or not 1 <= len(keyframes) <= 500:
            raise ValueError("keyframes must contain between 1 and 500 records")
        if policy not in {"REPLACE", "INSERT_ONLY"}:
            raise ValueError("policy must be REPLACE or INSERT_ONLY")
        _validate_key_style(interpolation, handle_left, handle_right)
        prepared = []
        seen = set()
        for index, source in enumerate(keyframes):
            record = dict(source)
            record["frame"] = _frame(record.get("frame"), f"keyframes[{index}].frame")
            record["_policy"] = policy
            resolved = _animation_owner(record)
            record["_value"] = _normalized_key_value(
                record.get("value"), resolved[2], record.get("array_index"), f"keyframes[{index}].value"
            )
            identity = (
                record["object_name"],
                record.get("owner", "OBJECT"),
                record.get("constraint_name"),
                record["data_path"],
                record.get("array_index"),
                record["frame"],
            )
            if identity in seen:
                raise ValueError(f"Duplicate keyframe destination at record {index}")
            seen.add(identity)
            id_owner = resolved[0].data if record.get("owner") in {"CAMERA_DATA", "DOF"} else resolved[0]
            indices = [record.get("array_index")] if record.get("array_index") is not None else range(resolved[2])
            if policy == "INSERT_ONLY" and any(
                _key_at(_find_fcurve(id_owner, resolved[3], array_index), record["frame"]) is not None
                for array_index in indices
            ):
                raise ValueError(f"A key already exists at record {index}; INSERT_ONLY made no changes")
            prepared.append((record, resolved))

        changed_keys = []
        for record, resolved in prepared:
            changed_keys.extend(_assign_and_key(record, resolved, interpolation, handle_left, handle_right))
        changed_objects = list(dict.fromkeys(record["object_name"] for record, _resolved in prepared))
        actions = sorted(
            {
                action.name
                for record, resolved in prepared
                for action in [
                    _action_fcurves(resolved[0].data if record["owner"] in {"CAMERA_DATA", "DOF"} else resolved[0])[0]
                ]
                if action is not None
            }
        )
        return {
            "keyframes": changed_keys,
            "actions": actions,
            "policy": policy,
            "changed_objects": changed_objects,
            "changed_resources": actions,
        }

    def set_camera_interpolation(
        self,
        object_name,
        owner,
        data_path,
        frame_start,
        frame_end,
        array_index=None,
        interpolation="BEZIER",
        handle_left="AUTO_CLAMPED",
        handle_right="AUTO_CLAMPED",
        easing=None,
    ):
        _validate_key_style(interpolation, handle_left, handle_right)
        start = _frame(frame_start, "frame_start")
        end = _frame(frame_end, "frame_end")
        if start > end:
            raise ValueError("frame_start must be less than or equal to frame_end")
        obj = _object(object_name)
        if owner == "OBJECT":
            if data_path not in _OBJECT_CHANNELS:
                raise ValueError(f"Unsupported object animation path: {data_path}")
            id_owner = obj
        elif owner == "CAMERA_DATA":
            if obj.type != "CAMERA" or data_path not in _CAMERA_CHANNELS | {f"dof.{path}" for path in _DOF_CHANNELS}:
                raise ValueError(f"Unsupported camera animation path: {data_path}")
            id_owner = obj.data
        else:
            raise ValueError("owner must be OBJECT or CAMERA_DATA")
        action, curves = _action_fcurves(id_owner)
        if action is None:
            raise ValueError(f"{object_name} has no action for {owner}")
        matched_curves = []
        changed = []
        for curve in curves:
            if curve.data_path != data_path or (array_index is not None and curve.array_index != array_index):
                continue
            matched_curves.append({"data_path": curve.data_path, "array_index": curve.array_index})
            for point in curve.keyframe_points:
                if start <= point.co[0] <= end:
                    point.interpolation = interpolation
                    if interpolation == "BEZIER":
                        point.handle_left_type = handle_left
                        point.handle_right_type = handle_right
                    if easing is not None and hasattr(point, "easing"):
                        point.easing = easing
                    changed.append({"array_index": curve.array_index, "frame": float(point.co[0])})
        if not matched_curves:
            raise ValueError("No matching animation curves were found")
        return {
            "object": obj.name,
            "owner": owner,
            "action": action.name,
            "matched_curves": matched_curves,
            "changed_keys": changed,
            "changed_objects": [obj.name] if changed else [],
            "changed_resources": [action.name] if changed else [],
        }

    def create_focus_pull(
        self,
        scene_name,
        camera_name,
        start_frame,
        end_frame,
        start_subject_name=None,
        start_point=None,
        end_subject_name=None,
        end_point=None,
        mode="DISTANCE",
        interpolation="BEZIER",
        focus_control_name="MCP Focus Pull",
        collection_name="MCP Camera Controls",
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene)
        start = _frame(start_frame, "start_frame")
        end = _frame(end_frame, "end_frame")
        if start >= end:
            raise ValueError("start_frame must be less than end_frame")
        if mode not in {"DISTANCE", "FOCUS_CONTROL"}:
            raise ValueError("mode must be DISTANCE or FOCUS_CONTROL")
        if interpolation not in _INTERPOLATIONS:
            raise ValueError(f"Unsupported interpolation: {interpolation}")
        for label, object_name, point in (
            ("start", start_subject_name, start_point),
            ("end", end_subject_name, end_point),
        ):
            if (object_name is None) == (point is None):
                raise ValueError(f"Supply exactly one {label} subject or point")
            if object_name is not None:
                _object(object_name, scene=scene)
            else:
                _vector(point, f"{label}_point")
        if mode == "FOCUS_CONTROL":
            _required_name(focus_control_name, "focus_control_name")
            collection = _ensure_collection(scene, collection_name)
        else:
            collection = None

        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        warnings = []
        try:
            scene.frame_set(start)
            start_world = _subject_point(scene, start_subject_name, start_point)
            start_depth = _focus_depth(camera, start_world)
            scene.frame_set(end)
            end_world = _subject_point(scene, end_subject_name, end_point)
            end_depth = _focus_depth(camera, end_world)
            for label, depth in (("start", start_depth), ("end", end_depth)):
                if depth <= 0:
                    warnings.append(
                        f"The {label} subject is behind the camera; focus uses the absolute camera-space depth."
                    )
                distance = abs(depth)
                if distance < camera.data.clip_start or distance > camera.data.clip_end:
                    warnings.append(f"The {label} focus plane is outside the camera clip interval.")
            if mode == "DISTANCE":
                camera.data.dof.use_dof = True
                camera.data.dof.focus_object = None
                path = camera.data.dof.path_from_id("focus_distance")
                for frame_value, depth in ((start, start_depth), (end, end_depth)):
                    camera.data.dof.focus_distance = max(abs(depth), 1e-6)
                    if not camera.data.dof.keyframe_insert(data_path="focus_distance", frame=frame_value):
                        raise RuntimeError(f"Blender refused the focus-distance key at frame {frame_value}")
                changed_keys = _set_interpolation_on_keys(camera.data, path, (start, end), interpolation)
                focus_control = None
            else:
                focus_control = bpy.data.objects.new(focus_control_name, None)
                collection.objects.link(focus_control)
                _tag(focus_control, str(uuid.uuid4()), "focus_pull")
                focus_control.empty_display_type = "SPHERE"
                focus_control.location = start_world
                focus_control.keyframe_insert(data_path="location", frame=start)
                focus_control.location = end_world
                focus_control.keyframe_insert(data_path="location", frame=end)
                changed_keys = _set_interpolation_on_keys(focus_control, "location", (start, end), interpolation)
                camera.data.dof.use_dof = True
                camera.data.dof.focus_object = focus_control
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
        action_owner = camera.data if mode == "DISTANCE" else focus_control
        action, _curves = _action_fcurves(action_owner)
        changed_objects = [camera.name] + ([focus_control.name] if focus_control is not None else [])
        return {
            "camera": camera.name,
            "mode": mode,
            "start": {"frame": start, "point": list(start_world), "camera_space_depth": start_depth},
            "end": {"frame": end, "point": list(end_world), "camera_space_depth": end_depth},
            "focus_control": getattr(focus_control, "name", None),
            "changed_keys": changed_keys,
            "warnings": warnings,
            "changed_objects": changed_objects,
            "changed_resources": [camera.data.name, *([action.name] if action is not None else [])],
        }

    def create_dolly_zoom(
        self,
        scene_name,
        camera_name,
        movement_object_name,
        start_frame,
        end_frame,
        start_distance,
        end_distance,
        subject_object_name=None,
        subject_point=None,
        subject_reference_size=1.0,
        start_lens=None,
        framing_axis="VERTICAL",
        interpolation="LINEAR",
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene)
        mover = _object(movement_object_name, scene=scene)
        if camera.data.type != "PERSP":
            raise ValueError("Dolly zoom requires a perspective camera")
        if framing_axis not in {"HORIZONTAL", "VERTICAL"}:
            raise ValueError("framing_axis must be HORIZONTAL or VERTICAL")
        start = _frame(start_frame, "start_frame")
        end = _frame(end_frame, "end_frame")
        if start >= end:
            raise ValueError("start_frame must be less than end_frame")
        start_distance = _finite_number(start_distance, "start_distance")
        end_distance = _finite_number(end_distance, "end_distance")
        if start_distance <= 0 or end_distance <= 0:
            raise ValueError("Dolly-zoom distances must be positive")
        subject_reference_size = _finite_number(subject_reference_size, "subject_reference_size")
        if subject_reference_size <= 0:
            raise ValueError("subject_reference_size must be positive")
        if (subject_object_name is None) == (subject_point is None):
            raise ValueError("Supply exactly one subject object or point")
        if subject_object_name is not None:
            _object(subject_object_name, scene=scene)
        else:
            _vector(subject_point, "subject_point")
        lens_start = camera.data.lens if start_lens is None else _finite_number(start_lens, "start_lens")
        lens_end = lens_start * end_distance / start_distance
        if not 1 <= lens_start <= 10_000 or not 1 <= lens_end <= 10_000:
            raise ValueError("Solved focal lengths must remain within Blender's 1-10000 mm range")
        if interpolation not in _INTERPOLATIONS:
            raise ValueError(f"Unsupported interpolation: {interpolation}")
        current_lens = camera.data.lens
        current_angle = camera.data.angle_x if framing_axis == "HORIZONTAL" else camera.data.angle_y
        current_tangent = math.tan(current_angle / 2)

        def projected_fraction(distance, lens):
            half_frame = distance * current_tangent * current_lens / lens
            return subject_reference_size / (2 * half_frame)

        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        solutions = []
        warnings = []
        try:
            for frame_value, distance, lens in ((start, start_distance, lens_start), (end, end_distance, lens_end)):
                scene.frame_set(frame_value)
                point = _subject_point(scene, subject_object_name, subject_point)
                camera_location = camera.matrix_world.translation.copy()
                away = camera_location - point
                if away.length_squared <= 1e-16:
                    away = -(camera.matrix_world.to_quaternion() @ mathutils.Vector((0.0, 0.0, -1.0)))
                away.normalize()
                desired_camera_location = point + away * distance
                delta = desired_camera_location - camera_location
                mover.matrix_world.translation = mover.matrix_world.translation + delta
                mover.keyframe_insert(data_path="location", frame=frame_value)
                camera.data.lens = lens
                camera.data.keyframe_insert(data_path="lens", frame=frame_value)
                _update_view_layer()
                actual_camera_location = camera.matrix_world.translation.copy()
                actual_distance = (actual_camera_location - point).length
                if not math.isclose(actual_distance, distance, rel_tol=1e-4, abs_tol=1e-4):
                    warnings.append(
                        f"At frame {frame_value}, constraints or parenting produced distance "
                        f"{actual_distance:.6g} instead of {distance:.6g}."
                    )
                if distance < camera.data.clip_start or distance > camera.data.clip_end:
                    warnings.append(f"The subject distance at frame {frame_value} is outside the camera clip interval.")
                solutions.append(
                    {
                        "frame": frame_value,
                        "distance": distance,
                        "lens": lens,
                        "subject_reference_size": subject_reference_size,
                        "projected_frame_fraction": projected_fraction(distance, lens),
                        "subject_point": list(point),
                        "camera_location": list(desired_camera_location),
                        "evaluated_camera_location": list(actual_camera_location),
                        "evaluated_distance": actual_distance,
                    }
                )
            move_keys = _set_interpolation_on_keys(mover, "location", (start, end), interpolation)
            lens_keys = _set_interpolation_on_keys(camera.data, "lens", (start, end), interpolation)
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
        move_action, _curves = _action_fcurves(mover)
        lens_action, _curves = _action_fcurves(camera.data)
        resources = [action.name for action in (move_action, lens_action) if action is not None]
        return {
            "camera": camera.name,
            "movement_object": mover.name,
            "framing_axis": framing_axis,
            "solutions": solutions,
            "changed_keys": [*move_keys, *lens_keys],
            "approximation": "Preserves the lens-to-camera-space-distance ratio for the subject reference point.",
            "warnings": warnings,
            "changed_objects": list(dict.fromkeys([camera.name, mover.name])),
            "changed_resources": [camera.data.name, *resources],
        }

    def add_camera_shake(
        self,
        scene_name,
        camera_name,
        collection_name,
        control_name,
        frame_start,
        frame_end,
        translation_strength=(0.02, 0.02, 0.01),
        rotation_strength=(0.01, 0.01, 0.02),
        noise_scale=12.0,
        phase=0.0,
        depth=1,
        influence=1.0,
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene)
        collection = _ensure_collection(scene, collection_name)
        _required_name(control_name, "control_name")
        start = _frame(frame_start, "frame_start")
        end = _frame(frame_end, "frame_end")
        if start >= end:
            raise ValueError("frame_start must be less than frame_end")
        translation = _vector(translation_strength, "translation_strength")
        rotation = _vector(rotation_strength, "rotation_strength")
        if any(value < 0 for value in (*translation, *rotation)):
            raise ValueError("Shake strength components must be non-negative")
        if not any((*translation, *rotation)):
            raise ValueError("At least one shake strength component must be non-zero")
        noise_scale = _finite_number(noise_scale, "noise_scale")
        phase = _finite_number(phase, "phase")
        influence = _finite_number(influence, "influence")
        if noise_scale <= 0 or not 0 <= influence <= 1 or isinstance(depth, bool) or not 0 <= int(depth) <= 8:
            raise ValueError("Invalid noise scale, influence, or depth")

        camera_world = camera.matrix_world.copy()
        original_parent = camera.parent
        original_parent_inverse = camera.matrix_parent_inverse.copy()
        original_basis = camera.matrix_basis.copy()
        control = bpy.data.objects.new(control_name, None)
        collection.objects.link(control)
        control.empty_display_type = "CUBE"
        control.empty_display_size = 0.35
        control.parent = original_parent
        control.matrix_parent_inverse = original_parent_inverse
        control.matrix_basis = mathutils.Matrix.Identity(4)
        _tag(control, str(uuid.uuid4()), "shake_control")
        camera.parent = control
        camera.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        camera.matrix_basis = original_basis
        if not _matrix_close(camera.matrix_world, camera_world):
            camera.matrix_world = camera_world
        control.rotation_mode = "XYZ"
        for path in ("location", "rotation_euler"):
            control.keyframe_insert(data_path=path, frame=start)
            control.keyframe_insert(data_path=path, frame=end)
        action, curves = _action_fcurves(control)
        if action is None:
            raise RuntimeError("Blender did not create an action for the shake control")
        modifiers = []
        strengths = {"location": translation, "rotation_euler": rotation}
        for curve in curves:
            if curve.data_path not in strengths:
                continue
            strength = strengths[curve.data_path][curve.array_index]
            if strength == 0:
                continue
            modifier = curve.modifiers.new(type="NOISE")
            modifier.strength = strength
            modifier.scale = noise_scale
            modifier.phase = phase + curve.array_index * 17.0 + (100.0 if curve.data_path == "rotation_euler" else 0.0)
            modifier.depth = int(depth)
            modifier.blend_type = "ADD"
            modifier.influence = influence
            modifier.use_restricted_range = True
            modifier.frame_start = start
            modifier.frame_end = end
            modifiers.append(
                {
                    "data_path": curve.data_path,
                    "array_index": curve.array_index,
                    "strength": strength,
                    "phase": modifier.phase,
                }
            )
        return {
            "camera": camera.name,
            "control": control.name,
            "action": action.name,
            "noise_modifiers": modifiers,
            "disable": f"Mute action '{action.name}' or set each Noise modifier influence to 0.",
            "changed_objects": [control.name, camera.name],
            "changed_resources": [action.name],
        }

    def create_camera_markers(self, scene_name, action, markers=None, replace_existing=False):
        scene = _scene(scene_name)
        edits = list(markers or [])
        if action not in {"LIST", "CREATE", "UPDATE", "REMOVE"}:
            raise ValueError("action must be LIST, CREATE, UPDATE, or REMOVE")
        if action == "LIST":
            if edits:
                raise ValueError("LIST does not accept marker edits")
            return {"action": action, "camera_cuts": _camera_cut_map(scene), "changed_objects": []}
        if not edits or len(edits) > 200:
            raise ValueError("A mutating marker request requires 1 to 200 edits")
        names = []
        prepared = []
        for index, edit in enumerate(edits):
            name = _required_name(edit.get("name"), f"markers[{index}].name")
            if name in names:
                raise ValueError(f"Duplicate marker name in request: {name}")
            names.append(name)
            existing = scene.timeline_markers.get(name)
            if action == "CREATE":
                if edit.get("frame") is None or edit.get("camera_name") is None:
                    raise ValueError("CREATE requires frame and camera_name for every marker")
                if existing is not None and not replace_existing:
                    raise ValueError(f"Marker already exists: {name}")
            elif action in {"UPDATE", "REMOVE"} and existing is None:
                raise ValueError(f"Marker not found: {name}")
            if action != "REMOVE":
                frame = _frame(edit.get("frame", existing.frame if existing else None), f"markers[{index}].frame")
                camera_name = edit.get("camera_name", getattr(existing.camera, "name", None) if existing else None)
                if camera_name is None:
                    raise ValueError(f"markers[{index}].camera_name is required")
                camera = _camera(camera_name, scene=scene)
            else:
                frame = None
                camera = None
            prepared.append((name, existing, frame, camera))
        changed_cameras = []
        for name, existing, frame, camera in prepared:
            if action == "REMOVE":
                scene.timeline_markers.remove(existing)
                continue
            marker = existing or scene.timeline_markers.new(name, frame=frame)
            marker.frame = frame
            marker.camera = camera
            changed_cameras.append(camera.name)
        return {
            "action": action,
            "edited_markers": names,
            "camera_cuts": _camera_cut_map(scene),
            "changed_objects": list(dict.fromkeys(changed_cameras)),
            "changed_resources": [scene.name],
        }

    def match_camera_transform(
        self,
        destination_name,
        policy="TRANSFORM_ONLY",
        source_object_name=None,
        world_transform=None,
    ):
        destination = _object(destination_name)
        if policy not in {"TRANSFORM_ONLY", "OPTICS_ONLY", "FULL"}:
            raise ValueError("Unsupported match policy")
        if (source_object_name is None) == (world_transform is None):
            raise ValueError("Supply exactly one source object or world transform")
        source = _object(source_object_name) if source_object_name is not None else None
        if policy != "TRANSFORM_ONLY" and (source is None or source.type != "CAMERA" or destination.type != "CAMERA"):
            raise ValueError("Optics matching requires source and destination camera objects")
        before = _transform_info(destination)
        optics = None
        if policy in {"TRANSFORM_ONLY", "FULL"}:
            if source is not None:
                desired_matrix = source.matrix_world.copy()
            else:
                location = _vector(world_transform.get("location"), "world_transform.location")
                scale = _vector(world_transform.get("scale", (1, 1, 1)), "world_transform.scale")
                quaternion_values = world_transform.get("rotation_quaternion")
                if quaternion_values is None or len(quaternion_values) != 4:
                    raise ValueError("world_transform.rotation_quaternion must contain four numbers")
                quaternion = mathutils.Quaternion(
                    tuple(_finite_number(value, "rotation_quaternion") for value in quaternion_values)
                )
                if quaternion.length_squared <= 1e-16:
                    raise ValueError("rotation_quaternion must not be zero")
                quaternion.normalize()
                desired_matrix = mathutils.Matrix.LocRotScale(list(location), quaternion, list(scale))
            destination.matrix_world = desired_matrix
        if policy in {"OPTICS_ONLY", "FULL"}:
            fields = [
                field
                for field in _OPTICS_COPY_FIELDS
                if hasattr(source.data, field) and hasattr(destination.data, field)
            ]
            optics = {
                "old": {field: _plain(getattr(destination.data, field)) for field in fields},
                "new": {field: _plain(getattr(source.data, field)) for field in fields},
            }
            destination.data.type = source.data.type
            for field in fields:
                setattr(destination.data, field, getattr(source.data, field))
        _update_view_layer()
        assigned = destination.matrix_world.copy()
        evaluated = destination.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
        return {
            "destination": destination.name,
            "source": getattr(source, "name", None),
            "policy": policy,
            "before": before,
            "after": _transform_info(destination),
            "optics": optics,
            "constraints_affect_evaluated_transform": not _matrix_close(assigned, evaluated),
            "changed_objects": [destination.name],
            "changed_resources": [destination.data.name] if policy != "TRANSFORM_ONLY" else [],
        }

    def duplicate_camera_rig(
        self,
        scene_name,
        source_root_name,
        collection_name,
        new_rig_name,
        camera_data_policy="COPY",
        path_data_policy="COPY",
        animation_policy="COPY",
        external_target_policy="SHARE",
    ):
        scene = _scene(scene_name)
        source_root = _object(source_root_name, scene=scene)
        rig_id = source_root.get("mcp_camera_rig_id")
        if not rig_id:
            raise ValueError(f"'{source_root.name}' is not tagged as an MCP camera rig")
        _required_name(new_rig_name, "new_rig_name")
        if camera_data_policy not in {"COPY", "LINK"} or path_data_policy not in {"COPY", "LINK"}:
            raise ValueError("Datablock policies must be COPY or LINK")
        if animation_policy not in {"COPY", "LINK", "NONE"}:
            raise ValueError("animation_policy must be COPY, LINK, or NONE")
        if external_target_policy not in {"SHARE", "REJECT"}:
            raise ValueError("external_target_policy must be SHARE or REJECT")
        members = [obj for obj in scene.objects if obj.get("mcp_camera_rig_id") == rig_id]
        if not members or len(members) > _MAX_RIG_MEMBERS:
            raise ValueError("Rig membership is empty or exceeds the 2000-object safety limit")
        member_set = set(members)
        external_objects = {
            constraint.target
            for obj in members
            for constraint in obj.constraints
            if getattr(constraint, "target", None) is not None and constraint.target not in member_set
        }
        external_objects.update(
            obj.parent for obj in members if obj.parent is not None and obj.parent not in member_set
        )
        for obj in members:
            animation = getattr(obj, "animation_data", None)
            for curve in getattr(animation, "drivers", ()) if animation is not None else ():
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        if isinstance(target.id, bpy.types.Object) and target.id not in member_set:
                            external_objects.add(target.id)
        external = sorted(obj.name for obj in external_objects)
        if external and external_target_policy == "REJECT":
            raise ValueError(f"Rig has external constraint targets: {external}")
        collection = _ensure_collection(scene, collection_name)
        new_id = str(uuid.uuid4())
        mapping = {}
        used_names = set()
        for index, source in enumerate(sorted(members, key=lambda item: item.name)):
            clone = source.copy()
            role = source.get("mcp_camera_role", "member")
            stem = f"{new_rig_name} {str(role).replace('_', ' ').title()}"
            candidate = stem if stem not in used_names else f"{stem} {index + 1}"
            clone.name = candidate
            used_names.add(clone.name)
            if source.data is not None:
                copy_data = (source.type == "CAMERA" and camera_data_policy == "COPY") or (
                    source.type == "CURVE" and path_data_policy == "COPY"
                )
                if copy_data:
                    clone.data = source.data.copy()
            collection.objects.link(clone)
            _tag(clone, new_id, role)
            clone["mcp_camera_source_rig_id"] = rig_id
            mapping[source] = clone
        for source, clone in mapping.items():
            if source.parent in mapping:
                clone.parent = mapping[source.parent]
            for constraint in clone.constraints:
                target = getattr(constraint, "target", None)
                if target in mapping:
                    constraint.target = mapping[target]
            animation = getattr(clone, "animation_data", None)
            if animation is not None:
                for curve in getattr(animation, "drivers", ()):
                    for variable in curve.driver.variables:
                        for target in variable.targets:
                            if target.id in mapping:
                                target.id = mapping[target.id]
            _copy_action(clone, animation_policy)
            if clone.data is not None and clone.data is not source.data:
                _copy_action(clone.data, animation_policy)
        clones = list(mapping.values())
        new_root = mapping[source_root]
        cameras = [obj for obj in clones if obj.type == "CAMERA"]
        actions = sorted(
            {
                animation.action.name
                for obj in clones
                for owner in (obj, obj.data)
                if owner is not None
                for animation in [getattr(owner, "animation_data", None)]
                if animation is not None and animation.action is not None
            }
        )
        resources = [
            clone.data.name
            for source, clone in mapping.items()
            if clone.data is not None and clone.data is not source.data
        ]
        if animation_policy == "COPY":
            resources.extend(actions)
        return {
            "source_rig_id": rig_id,
            "rig_id": new_id,
            "root": new_root.name,
            "collection": collection.name,
            "members": [
                {"source": source.name, "name": clone.name, "role": clone.get("mcp_camera_role"), "type": clone.type}
                for source, clone in mapping.items()
            ],
            "cameras": [camera.name for camera in cameras],
            "external_targets": external,
            "sharing": {
                "camera_data": camera_data_policy,
                "path_data": path_data_policy,
                "animation": animation_policy,
                "external_targets": external_target_policy,
            },
            "actions": actions,
            "changed_objects": [obj.name for obj in clones],
            "changed_resources": list(dict.fromkeys([*resources, *actions])),
        }

    def add_camera_constraint(
        self,
        scene_name,
        owner_name,
        constraint_name,
        constraint_type,
        target_name=None,
        subtarget=None,
        influence=1.0,
        owner_space="WORLD",
        target_space="WORLD",
        stack_index=-1,
        preserve_transform=True,
        track_axis="TRACK_NEGATIVE_Z",
        up_axis="UP_Y",
        lock_axis="LOCK_Y",
        forward_axis="FORWARD_X",
        use_curve_follow=True,
        use_fixed_location=True,
        offset_factor=0.0,
        use_x=True,
        use_y=True,
        use_z=True,
        invert_x=False,
        invert_y=False,
        invert_z=False,
        minimum=None,
        maximum=None,
    ):
        scene = _scene(scene_name)
        owner = _object(owner_name, scene=scene)
        _required_name(constraint_name, "constraint_name")
        if constraint_type not in _CONSTRAINT_TYPES:
            raise ValueError(f"Unsupported camera constraint: {constraint_type}")
        valid_spaces = {"WORLD", "CUSTOM", "POSE", "LOCAL_WITH_PARENT", "LOCAL"}
        if owner_space not in valid_spaces or target_space not in valid_spaces:
            raise ValueError("Unsupported owner_space or target_space")
        if track_axis not in {
            "TRACK_X",
            "TRACK_Y",
            "TRACK_Z",
            "TRACK_NEGATIVE_X",
            "TRACK_NEGATIVE_Y",
            "TRACK_NEGATIVE_Z",
        }:
            raise ValueError("Unsupported track_axis")
        if up_axis not in {"UP_X", "UP_Y", "UP_Z"} or lock_axis not in {"LOCK_X", "LOCK_Y", "LOCK_Z"}:
            raise ValueError("Unsupported up_axis or lock_axis")
        if forward_axis not in {
            "FORWARD_X",
            "FORWARD_Y",
            "FORWARD_Z",
            "TRACK_NEGATIVE_X",
            "TRACK_NEGATIVE_Y",
            "TRACK_NEGATIVE_Z",
        }:
            raise ValueError("Unsupported forward_axis")
        if isinstance(stack_index, bool) or int(stack_index) != stack_index or int(stack_index) < -1:
            raise ValueError("stack_index must be an integer greater than or equal to -1")
        offset_factor = _finite_number(offset_factor, "offset_factor")
        if not 0 <= offset_factor <= 1:
            raise ValueError("offset_factor must be between 0 and 1")
        targeted = constraint_type in _TARGETED_CONSTRAINTS
        if targeted != (target_name is not None):
            raise ValueError("The target requirement does not match the constraint type")
        target = _object(target_name, scene=scene) if target_name is not None else None
        if constraint_type == "FOLLOW_PATH" and target.type != "CURVE":
            raise ValueError("FOLLOW_PATH requires a curve target")
        if subtarget:
            bones = getattr(getattr(target, "data", None), "bones", None)
            if target.type != "ARMATURE" or bones is None or bones.get(subtarget) is None:
                raise ValueError(f"Bone subtarget not found: {subtarget}")
        if constraint_type.startswith("LIMIT_") and minimum is None and maximum is None:
            raise ValueError("Limit constraints require minimum and/or maximum")
        minimum_vector = _vector(minimum, "minimum") if minimum is not None else None
        maximum_vector = _vector(maximum, "maximum") if maximum is not None else None
        if (
            minimum_vector is not None
            and maximum_vector is not None
            and any(lower > upper for lower, upper in zip(minimum_vector, maximum_vector, strict=True))
        ):
            raise ValueError("Each minimum component must be less than or equal to maximum")
        influence = _finite_number(influence, "influence")
        if not 0 <= influence <= 1:
            raise ValueError("influence must be between 0 and 1")
        existing = owner.constraints.get(constraint_name)
        if existing is not None and existing.type != constraint_type:
            raise ValueError(f"Constraint '{constraint_name}' already has type {existing.type}")
        before_matrix = owner.matrix_world.copy()
        constraint = existing or owner.constraints.new(type=constraint_type)
        if existing is None:
            constraint.name = constraint_name
        fields: dict[str, object] = {"influence": influence}
        if target is not None:
            fields.update({"target": target, "subtarget": subtarget or ""})
        if constraint_type in {"TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK"}:
            fields["track_axis"] = track_axis
        if constraint_type == "TRACK_TO":
            fields["up_axis"] = up_axis
        if constraint_type == "LOCKED_TRACK":
            fields["lock_axis"] = lock_axis
        if constraint_type == "FOLLOW_PATH":
            fields.update(
                {
                    "forward_axis": forward_axis,
                    "up_axis": up_axis,
                    "use_curve_follow": use_curve_follow,
                    "use_fixed_location": use_fixed_location,
                    "offset_factor": offset_factor,
                }
            )
        if constraint_type in _COPY_CONSTRAINTS:
            fields.update(
                {
                    "use_x": use_x,
                    "use_y": use_y,
                    "use_z": use_z,
                    "invert_x": invert_x,
                    "invert_y": invert_y,
                    "invert_z": invert_z,
                }
            )
        for field in ("owner_space", "target_space"):
            if hasattr(constraint, field):
                fields[field] = owner_space if field == "owner_space" else target_space
        if constraint_type.startswith("LIMIT_"):
            axes = ("x", "y", "z")
            for index, axis in enumerate(axes):
                if minimum_vector is not None:
                    fields[f"use_min_{axis}"] = True
                    fields[f"min_{axis}"] = minimum_vector[index]
                if maximum_vector is not None:
                    fields[f"use_max_{axis}"] = True
                    fields[f"max_{axis}"] = maximum_vector[index]
        unsupported = [field for field in fields if not hasattr(constraint, field)]
        if unsupported:
            if existing is None:
                owner.constraints.remove(constraint)
            raise ValueError(f"Constraint {constraint_type} does not support fields: {unsupported}")
        old_fields = {field: getattr(constraint, field) for field in fields}
        old_index = list(owner.constraints).index(constraint)
        old_inverse = constraint.inverse_matrix.copy() if hasattr(constraint, "inverse_matrix") else None
        try:
            for field, value in fields.items():
                setattr(constraint, field, value)
            if constraint_type == "CHILD_OF" and preserve_transform:
                constraint.inverse_matrix = target.matrix_world.inverted_safe() @ before_matrix
            if stack_index >= 0:
                source_index = list(owner.constraints).index(constraint)
                owner.constraints.move(source_index, min(int(stack_index), len(owner.constraints) - 1))
            if preserve_transform and constraint_type != "CHILD_OF":
                owner.matrix_world = before_matrix
        except Exception:
            owner.matrix_world = before_matrix
            if existing is None:
                owner.constraints.remove(constraint)
            else:
                for field, value in old_fields.items():
                    setattr(constraint, field, value)
                if old_inverse is not None:
                    constraint.inverse_matrix = old_inverse
                owner.constraints.move(list(owner.constraints).index(constraint), old_index)
            raise
        _update_view_layer()
        evaluated = owner.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
        return {
            "owner": owner.name,
            "constraint": _constraint_info(constraint),
            "created": existing is None,
            "assigned_world_matrix": _matrix_list(owner.matrix_world),
            "evaluated_world_matrix": _matrix_list(evaluated),
            "evaluated_transform_changed": not _matrix_close(before_matrix, evaluated),
            "changed_objects": [owner.name],
        }

    def configure_camera_render_gate(
        self,
        scene_name,
        camera_name=None,
        render=None,
        border=None,
        safe_areas=None,
        guides=None,
    ):
        scene = _scene(scene_name)
        camera = _camera(camera_name, scene=scene) if camera_name is not None else None
        render_patch = dict(render or {})
        border_patch = dict(border or {})
        safe_patch = dict(safe_areas or {})
        guide_patch = dict(guides or {})
        if not any((render_patch, border_patch, safe_patch, guide_patch)):
            raise ValueError("Provide at least one render-gate field")
        render_allowed = {"resolution_x", "resolution_y", "resolution_percentage", "pixel_aspect_x", "pixel_aspect_y"}
        border_map = {
            "use_border": "use_border",
            "use_crop_to_border": "use_crop_to_border",
            "min_x": "border_min_x",
            "max_x": "border_max_x",
            "min_y": "border_min_y",
            "max_y": "border_max_y",
        }
        if set(render_patch) - render_allowed or set(border_patch) - set(border_map):
            raise ValueError("Unsupported render-gate field")
        for field in ("resolution_x", "resolution_y"):
            if field in render_patch and (
                isinstance(render_patch[field], bool)
                or int(render_patch[field]) != render_patch[field]
                or not 4 <= int(render_patch[field]) <= 65_536
            ):
                raise ValueError(f"{field} must be an integer between 4 and 65536")
        if "resolution_percentage" in render_patch and (
            isinstance(render_patch["resolution_percentage"], bool)
            or int(render_patch["resolution_percentage"]) != render_patch["resolution_percentage"]
            or not 1 <= int(render_patch["resolution_percentage"]) <= 100
        ):
            raise ValueError("resolution_percentage must be an integer between 1 and 100")
        for field in ("pixel_aspect_x", "pixel_aspect_y"):
            if field in render_patch and not 0 < _finite_number(render_patch[field], field) <= 200:
                raise ValueError(f"{field} must be positive and at most 200")
        for field in ("min_x", "max_x", "min_y", "max_y"):
            if field in border_patch and not 0 <= _finite_number(border_patch[field], field) <= 1:
                raise ValueError(f"{field} must be between 0 and 1")
        current_min_x = border_patch.get("min_x", scene.render.border_min_x)
        current_max_x = border_patch.get("max_x", scene.render.border_max_x)
        current_min_y = border_patch.get("min_y", scene.render.border_min_y)
        current_max_y = border_patch.get("max_y", scene.render.border_max_y)
        if current_min_x >= current_max_x or current_min_y >= current_max_y:
            raise ValueError("Render border minima must be less than maxima")
        safe_allowed = {"title", "action", "title_center", "action_center"}
        if set(safe_patch) - safe_allowed:
            raise ValueError("Unsupported safe-area field")
        for field, value in safe_patch.items():
            if len(value) != 2 or any(not 0 <= _finite_number(item, field) <= 1 for item in value):
                raise ValueError(f"{field} must contain two values between 0 and 1")
            if not hasattr(scene.safe_areas, field):
                raise ValueError(f"Running Blender does not support safe-area field: {field}")
        if guide_patch and camera is None:
            raise ValueError("camera_name is required for camera guides")
        guide_patch = _validate_display(guide_patch)
        if set(guide_patch) - _CAMERA_GUIDES:
            raise ValueError("Unsupported camera guide field")
        missing_guides = (
            [field for field in guide_patch if not hasattr(camera.data, field)] if camera is not None else []
        )
        if missing_guides:
            raise ValueError(f"Running Blender does not support camera guide fields: {missing_guides}")
        old = {"render": {}, "border": {}, "safe_areas": {}, "guides": {}}
        new = {"render": {}, "border": {}, "safe_areas": {}, "guides": {}}
        try:
            for field, value in render_patch.items():
                old["render"][field] = _plain(getattr(scene.render, field))
                setattr(scene.render, field, value)
                new["render"][field] = _plain(getattr(scene.render, field))
            for public, rna in border_map.items():
                if public in border_patch:
                    old["border"][public] = _plain(getattr(scene.render, rna))
                    setattr(scene.render, rna, border_patch[public])
                    new["border"][public] = _plain(getattr(scene.render, rna))
            for field, value in safe_patch.items():
                old["safe_areas"][field] = _plain(getattr(scene.safe_areas, field))
                setattr(scene.safe_areas, field, value)
                new["safe_areas"][field] = _plain(getattr(scene.safe_areas, field))
            if guide_patch:
                old["guides"], new["guides"] = _patch_values(camera.data, guide_patch, _CAMERA_GUIDES)
        except Exception:
            for field, value in old["render"].items():
                setattr(scene.render, field, value)
            for public, value in old["border"].items():
                setattr(scene.render, border_map[public], value)
            for field, value in old["safe_areas"].items():
                setattr(scene.safe_areas, field, value)
            for field, value in old["guides"].items():
                setattr(camera.data, field, value)
            raise
        changed_resources = [scene.name] + ([camera.data.name] if guide_patch else [])
        return {
            "scene": scene.name,
            "camera": getattr(camera, "name", None),
            "old": old,
            "new": new,
            "changed_objects": [camera.name] if guide_patch else [],
            "changed_resources": changed_resources,
        }

    def validate_camera_rig(self, scene_name, object_names=None, sample_frames=None):
        scene = _scene(scene_name)
        frames = list(sample_frames or [scene.frame_current])
        if len(frames) > 24:
            raise ValueError("sample_frames exceeds the 24-frame safety limit")
        frames = list(dict.fromkeys(_frame(value, "sample_frames") for value in frames))
        if object_names is None:
            objects = [obj for obj in scene.objects if obj.type == "CAMERA" or obj.get("mcp_camera_rig_id") is not None]
            if len(objects) > 500:
                raise ValueError("Scene camera-rig scope exceeds 500 objects; provide an explicit object_names subset")
        else:
            if len(object_names) > 500 or len(set(object_names)) != len(object_names):
                raise ValueError("object_names must be unique and contain at most 500 names")
            objects = [_object(name, scene=scene) for name in object_names]
        findings = []
        if scene.camera is None:
            findings.append(
                _finding(
                    "WARNING",
                    "MISSING_SCENE_CAMERA",
                    scene.name,
                    "scene.camera",
                    "The scene has no active camera.",
                    "Assign a camera before rendering the shot.",
                )
            )
        elif scene.camera.type != "CAMERA":
            findings.append(
                _finding(
                    "ERROR",
                    "INVALID_SCENE_CAMERA",
                    scene.camera,
                    "scene.camera",
                    "The active scene object is not a camera.",
                    "Assign a camera object.",
                )
            )
        roots_by_id = {}
        roles_by_id = {}
        for obj in objects:
            rig_id = obj.get("mcp_camera_rig_id")
            if rig_id:
                roles_by_id.setdefault(rig_id, {}).setdefault(obj.get("mcp_camera_role"), []).append(obj)
                if obj.parent is None:
                    roots_by_id.setdefault(rig_id, []).append(obj)
            seen = set()
            parent = obj
            while parent is not None:
                if parent in seen:
                    findings.append(
                        _finding(
                            "ERROR", "PARENT_CYCLE", obj, "parent", "Parent cycle detected.", "Break the parent cycle."
                        )
                    )
                    break
                seen.add(parent)
                parent = parent.parent
            parent = obj.parent
            if parent is not None:
                scale = parent.matrix_world.to_scale()
                if any(value < 0 for value in scale):
                    findings.append(
                        _finding(
                            "WARNING",
                            "NEGATIVE_PARENT_SCALE",
                            obj,
                            "parent.scale",
                            "A parent has negative scale.",
                            "Use positive parent scale or verify evaluated camera orientation.",
                        )
                    )
                if max(scale) - min(scale) > 1e-5:
                    findings.append(
                        _finding(
                            "WARNING",
                            "NONUNIFORM_PARENT_SCALE",
                            obj,
                            "parent.scale",
                            "A parent has nonuniform scale.",
                            "Verify constraints and camera transforms under evaluated scale.",
                        )
                    )
            for constraint in obj.constraints:
                if not constraint.is_valid:
                    findings.append(
                        _finding(
                            "ERROR",
                            "INVALID_CONSTRAINT",
                            obj,
                            f'constraints["{constraint.name}"]',
                            "Constraint reports invalid state.",
                            "Repair or remove the broken constraint.",
                        )
                    )
                if constraint.type in _TARGETED_CONSTRAINTS and getattr(constraint, "target", None) is None:
                    findings.append(
                        _finding(
                            "ERROR",
                            "MISSING_CONSTRAINT_TARGET",
                            obj,
                            f'constraints["{constraint.name}"].target',
                            "Constraint target is missing.",
                            "Assign an explicit compatible target.",
                        )
                    )
                if (
                    constraint.type == "FOLLOW_PATH"
                    and getattr(getattr(constraint, "target", None), "type", None) != "CURVE"
                ):
                    findings.append(
                        _finding(
                            "ERROR",
                            "INVALID_PATH_TARGET",
                            obj,
                            f'constraints["{constraint.name}"].target',
                            "Follow Path target is not a curve.",
                            "Assign a curve object.",
                        )
                    )
            animation = getattr(obj, "animation_data", None)
            for curve in getattr(animation, "drivers", ()) if animation is not None else ():
                for variable in curve.driver.variables:
                    if any(target.id is None for target in variable.targets):
                        findings.append(
                            _finding(
                                "ERROR",
                                "BROKEN_DRIVER_TARGET",
                                obj,
                                curve.data_path,
                                "Driver variable has a missing ID target.",
                                "Repair the driver variable target.",
                            )
                        )
            if obj.type == "CAMERA":
                data = obj.data
                if data.clip_start <= 0 or data.clip_start >= data.clip_end:
                    findings.append(
                        _finding(
                            "ERROR",
                            "INVALID_CLIP_RANGE",
                            obj,
                            "data.clip_start",
                            "Camera clipping planes are invalid.",
                            "Set 0 < clip_start < clip_end.",
                        )
                    )
                if data.lens < 5 or data.lens > 500:
                    findings.append(
                        _finding(
                            "WARNING",
                            "EXTREME_LENS",
                            obj,
                            "data.lens",
                            "Camera lens is outside the typical 5-500 mm production range.",
                            "Confirm the extreme focal length is intentional.",
                        )
                    )
                if data.sensor_width <= 0 or data.sensor_height <= 0:
                    findings.append(
                        _finding(
                            "ERROR",
                            "INVALID_SENSOR",
                            obj,
                            "data.sensor_width",
                            "Camera sensor dimensions are invalid.",
                            "Set positive sensor dimensions.",
                        )
                    )
                if getattr(data, "users", 1) > 1:
                    findings.append(
                        _finding(
                            "INFO",
                            "SHARED_CAMERA_DATA",
                            obj,
                            "data",
                            "Camera datablock is shared by multiple objects.",
                            "Confirm linked optics are intentional.",
                        )
                    )
        for rig_id, roots in roots_by_id.items():
            if len(roots) != 1:
                findings.append(
                    _finding(
                        "WARNING",
                        "DUPLICATE_RIG_ROOT",
                        rig_id,
                        "mcp_camera_rig_id",
                        f"Rig has {len(roots)} root objects.",
                        "Keep one tagged root per rig ID.",
                    )
                )
        for rig_id, roles in roles_by_id.items():
            for role, members in roles.items():
                if role and len(members) > 1 and role in {"root", "camera", "target"}:
                    findings.append(
                        _finding(
                            "WARNING",
                            "DUPLICATE_RIG_ROLE",
                            rig_id,
                            str(role),
                            f"Rig has {len(members)} members with role '{role}'.",
                            "Assign unique primary roles.",
                        )
                    )
        markers_by_frame = {}
        for marker in scene.timeline_markers:
            if marker.camera is not None:
                markers_by_frame.setdefault(marker.frame, []).append(marker)
        for frame_value, markers in markers_by_frame.items():
            if len(markers) > 1:
                findings.append(
                    _finding(
                        "WARNING",
                        "OVERLAPPING_CAMERA_MARKERS",
                        scene.name,
                        "timeline_markers",
                        f"Multiple camera markers exist at frame {frame_value}.",
                        "Keep one editorial camera binding per frame.",
                        frame_value,
                    )
                )
        original_frame = scene.frame_current
        original_subframe = scene.frame_subframe
        try:
            for frame_value in frames:
                scene.frame_set(frame_value)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                for camera in (obj for obj in objects if obj.type == "CAMERA"):
                    evaluated = camera.evaluated_get(depsgraph)
                    for constraint in camera.constraints:
                        target = getattr(constraint, "target", None)
                        if target is None or constraint.type not in {"TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK"}:
                            continue
                        local = (
                            evaluated.matrix_world.inverted_safe()
                            @ target.evaluated_get(depsgraph).matrix_world.translation
                        )
                        if local.z >= 0:
                            findings.append(
                                _finding(
                                    "WARNING",
                                    "AIM_TARGET_BEHIND_CAMERA",
                                    camera,
                                    f'constraints["{constraint.name}"].target',
                                    "Aim target evaluates behind the camera.",
                                    "Move the target in front of camera local -Z or inspect the constraint axes.",
                                    frame_value,
                                )
                            )
                    focus = camera.data.dof.focus_object
                    if focus is not None:
                        local = (
                            evaluated.matrix_world.inverted_safe()
                            @ focus.evaluated_get(depsgraph).matrix_world.translation
                        )
                        if local.z >= 0:
                            findings.append(
                                _finding(
                                    "WARNING",
                                    "FOCUS_TARGET_BEHIND_CAMERA",
                                    camera,
                                    "data.dof.focus_object",
                                    "Focus target evaluates behind the camera.",
                                    "Move the focus target in front of camera local -Z.",
                                    frame_value,
                                )
                            )
        finally:
            scene.frame_set(original_frame, subframe=original_subframe)
        findings.sort(
            key=lambda item: (
                {"ERROR": 0, "WARNING": 1, "INFO": 2}[item["severity"]],
                item["code"],
                str(item["object"]),
            )
        )
        return {
            "scene": scene.name,
            "objects_checked": [obj.name for obj in objects],
            "sampled_frames": frames,
            "findings": findings,
            "summary": {
                severity.lower(): sum(item["severity"] == severity for item in findings)
                for severity in ("ERROR", "WARNING", "INFO")
            },
            "verification": "Structural and evaluated-transform checks only; visual correctness was not inferred.",
        }
