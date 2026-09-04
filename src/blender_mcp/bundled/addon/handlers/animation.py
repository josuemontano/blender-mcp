# ruff: file-ignore[missing-return-type-private-function, missing-return-type-undocumented-public-function, missing-type-function-argument, no-self-use, too-many-arguments, too-many-branches, too-many-locals, too-many-positional-arguments, undocumented-public-method]
"""Blender-side generic animation and layered Action handlers."""

import ast
import math
import re

import bpy


_TARGET_COLLECTIONS = {
    "OBJECT": "objects",
    "SCENE": "scenes",
    "MATERIAL": "materials",
    "WORLD": "worlds",
    "CAMERA": "cameras",
    "LIGHT": "lights",
    "MESH": "meshes",
    "CURVE": "curves",
    "ARMATURE": "armatures",
    "SHAPE_KEYS": "shape_keys",
    "NODE_GROUP": "node_groups",
}
_INTERPOLATIONS = {"CONSTANT", "LINEAR", "BEZIER"}
_NLA_TRACK_PROPERTIES = {"mute", "solo", "lock"}
_NLA_STRIP_PROPERTIES = {
    "frame_start",
    "frame_end",
    "action_frame_start",
    "action_frame_end",
    "blend_type",
    "extrapolation",
    "influence",
    "repeat",
    "scale",
    "mute",
}
_DRIVER_TYPES = {"AVERAGE", "SUM", "SCRIPTED", "MIN", "MAX"}
_DRIVER_VARIABLE_TYPES = {"SINGLE_PROP", "TRANSFORMS"}
_DRIVER_TRANSFORM_TYPES = {
    "LOC_X",
    "LOC_Y",
    "LOC_Z",
    "ROT_X",
    "ROT_Y",
    "ROT_Z",
    "ROT_W",
    "SCALE_X",
    "SCALE_Y",
    "SCALE_Z",
    "SCALE_AVG",
}
_DRIVER_TRANSFORM_SPACES = {"WORLD_SPACE", "TRANSFORM_SPACE", "LOCAL_SPACE"}
_DRIVER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_EXPRESSION_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)


def _required_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _target(spec):
    if not isinstance(spec, dict):
        raise ValueError("target must be an object")
    target_type = str(spec.get("type", "")).upper()
    collection_name = _TARGET_COLLECTIONS.get(target_type)
    if collection_name is None:
        raise ValueError(f"Unsupported animation target type: {target_type}")
    name = _required_name(spec.get("name"), "target.name")
    owner = getattr(bpy.data, collection_name).get(name)
    if owner is None:
        raise ValueError(f"{target_type} animation target not found: {name}")
    return owner, target_type


def _animation_data(owner, *, create=False):
    data = owner.animation_data_create() if create else owner.animation_data
    if create and data is None:
        raise ValueError(f"Animation data is unavailable for {owner.name}")
    return data


def _action_slot(action, owner, *, create=False):
    data = owner.animation_data
    if data and data.action == action and data.action_slot is not None:
        return data.action_slot
    matching = [slot for slot in action.slots if slot.target_id_type == owner.id_type]
    named = [slot for slot in matching if slot.name_display == owner.name]
    if len(named) == 1:
        return named[0]
    if len(matching) == 1:
        return matching[0]
    if matching and not create:
        raise ValueError(f"Action {action.name} has multiple suitable slots; assign the intended slot in Blender")
    if matching:
        raise ValueError(f"Action {action.name} has multiple suitable slots for {owner.id_type}")
    if not create:
        return None
    return action.slots.new(owner.id_type, owner.name)


def _assign_action(owner, action, *, replace_active):
    data = _animation_data(owner, create=True)
    current = data.action
    if current is not None and current != action and not replace_active:
        raise ValueError(
            f"{owner.name} already uses Action {current.name}; set replace_active=True to replace the assignment"
        )
    slot = _action_slot(action, owner, create=True)
    data.action = action
    data.action_slot = slot
    return slot


def _channelbag(action, slot, *, create=False):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                if bag.slot_handle == slot.handle:
                    return bag
    if not create:
        return None
    if not action.layers:
        layer = action.layers.new("MCP Layer")
    else:
        layer = action.layers[0]
    if not layer.strips:
        strip = layer.strips.new(type="KEYFRAME")
    else:
        strip = layer.strips[0]
    return strip.channelbags.new(slot)


def _iter_fcurves(action, slot_handle=None):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                if slot_handle is not None and bag.slot_handle != slot_handle:
                    continue
                for fcurve in bag.fcurves:
                    yield bag, fcurve


def _keyframe_info(action, slot_handle):
    records = []
    for _bag, fcurve in _iter_fcurves(action, slot_handle):
        for point in fcurve.keyframe_points:
            records.append(
                {
                    "data_path": fcurve.data_path,
                    "array_index": fcurve.array_index,
                    "frame": point.co[0],
                    "value": point.co[1],
                    "interpolation": point.interpolation,
                    "group": fcurve.group.name if fcurve.group else None,
                }
            )
    records.sort(key=lambda item: (item["frame"], item["data_path"], item["array_index"]))
    return records


def _driver_info(data):
    if data is None:
        return []
    records = []
    for fcurve in data.drivers:
        driver = fcurve.driver
        records.append(
            {
                "data_path": fcurve.data_path,
                "array_index": fcurve.array_index,
                "type": driver.type,
                "expression": driver.expression if driver.type == "SCRIPTED" else None,
                "muted": fcurve.mute,
                "valid": driver.is_valid,
                "variables": [
                    {
                        "name": variable.name,
                        "type": variable.type,
                        "targets": [
                            {
                                "id_type": target.id_type,
                                "id": target.id.name if target.id else None,
                                "data_path": target.data_path,
                            }
                            for target in variable.targets
                        ],
                    }
                    for variable in driver.variables
                ],
            }
        )
    return records


def _nla_info(data):
    if data is None:
        return []
    return [
        {
            "name": track.name,
            "mute": track.mute,
            "solo": track.is_solo,
            "lock": track.lock,
            "strips": [
                {
                    "name": strip.name,
                    "action": strip.action.name if strip.action else None,
                    "frame_start": strip.frame_start,
                    "frame_end": strip.frame_end,
                    "action_frame_start": strip.action_frame_start,
                    "action_frame_end": strip.action_frame_end,
                    "blend_type": strip.blend_type,
                    "extrapolation": strip.extrapolation,
                    "influence": strip.influence,
                    "repeat": strip.repeat,
                    "scale": strip.scale,
                    "mute": strip.mute,
                }
                for strip in track.strips
            ],
        }
        for track in data.nla_tracks
    ]


def _resolve_property(owner, data_path):
    data_path = _required_name(data_path, "data_path")
    owner_path, separator, property_name = data_path.rpartition(".")
    if not separator:
        property_name = data_path
        property_owner = owner
    else:
        try:
            property_owner = owner.path_resolve(owner_path)
        except Exception as exc:
            raise ValueError(f"Invalid data_path owner {owner_path!r}: {exc}") from exc
    if not property_name.isidentifier():
        raise ValueError("data_path must end in an RNA property identifier")
    properties = getattr(getattr(property_owner, "bl_rna", None), "properties", None)
    rna_property = properties.get(property_name) if properties is not None else None
    if rna_property is None:
        raise ValueError(f"RNA property not found: {data_path}")
    if rna_property.is_readonly:
        raise ValueError(f"RNA property is read-only: {data_path}")
    if not rna_property.is_animatable:
        raise ValueError(f"RNA property is not animatable: {data_path}")
    value = getattr(property_owner, property_name)
    array_length = getattr(rna_property, "array_length", 0)
    return array_length, value


def _expanded_edit(owner, edit):
    if not isinstance(edit, dict):
        raise ValueError("Each keyframe edit must be an object")
    operation = str(edit.get("operation", "UPSERT")).upper()
    if operation not in {"UPSERT", "REMOVE"}:
        raise ValueError(f"Unsupported keyframe operation: {operation}")
    data_path = _required_name(edit.get("data_path"), "data_path")
    frame = edit.get("frame")
    if isinstance(frame, bool) or not isinstance(frame, (int, float)) or not math.isfinite(frame):
        raise ValueError("frame must be a finite number")
    if not -1_000_000 <= frame <= 1_000_000:
        raise ValueError("frame must be between -1000000 and 1000000")
    index = edit.get("array_index", -1)
    if isinstance(index, bool) or not isinstance(index, int) or not -1 <= index <= 63:
        raise ValueError("array_index must be an integer from -1 to 63")
    interpolation = str(edit.get("interpolation", "BEZIER")).upper()
    if interpolation not in _INTERPOLATIONS:
        raise ValueError(f"Unsupported interpolation: {interpolation}")
    array_length, _current = _resolve_property(owner, data_path)
    if index >= 0 and (not array_length or index >= array_length):
        raise ValueError(f"array_index {index} is invalid for {data_path} (length {array_length})")
    value = edit.get("value")
    if operation == "REMOVE":
        if value is not None:
            raise ValueError("REMOVE does not accept value")
        indices = range(array_length) if index == -1 and array_length else [0 if index == -1 else index]
        return [(operation, data_path, item, float(frame), None, interpolation, edit.get("group")) for item in indices]
    if value is None:
        raise ValueError("UPSERT requires value")
    if index == -1 and array_length:
        if not isinstance(value, (list, tuple)) or len(value) != array_length:
            raise ValueError(f"{data_path} requires exactly {array_length} values when array_index=-1")
        values = list(value)
        indices = range(array_length)
    else:
        if isinstance(value, (list, tuple)):
            raise ValueError("A scalar value is required for one animation channel")
        values = [value]
        indices = [0 if index == -1 else index]
    expanded = []
    for channel_index, channel_value in zip(indices, values, strict=True):
        if isinstance(channel_value, bool) or not isinstance(channel_value, (int, float)):
            raise ValueError("Keyframe values must be numeric")
        channel_value = float(channel_value)
        if not math.isfinite(channel_value):
            raise ValueError("Keyframe values must be finite")
        expanded.append(
            (operation, data_path, channel_index, float(frame), channel_value, interpolation, edit.get("group"))
        )
    return expanded


def _find_key(fcurve, frame):
    return next((point for point in fcurve.keyframe_points if abs(point.co[0] - frame) <= 1e-6), None)


def _driver_fcurve(owner, data_path, index):
    data = owner.animation_data
    if data is None:
        return None
    return next(
        (fcurve for fcurve in data.drivers if fcurve.data_path == data_path and fcurve.array_index == index),
        None,
    )


def _safe_expression(expression, variable_names):
    if not isinstance(expression, str) or not expression or len(expression) > 256:
        raise ValueError("expression must contain between 1 and 256 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("expression must be valid arithmetic syntax") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_EXPRESSION_NODES):
            raise ValueError("expression may contain only arithmetic, numeric constants, variables, and frame")
        if isinstance(node, ast.Name) and node.id not in variable_names | {"frame"}:
            raise ValueError(f"expression references undeclared variable: {node.id}")
        if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, (int, float))):
            raise ValueError("expression constants must be numeric")
    return expression


def _prepare_driver_variables(variables):
    if not isinstance(variables, list) or len(variables) > 64:
        raise ValueError("variables must be a list with at most 64 records")
    prepared = []
    names = set()
    for spec in variables:
        if not isinstance(spec, dict):
            raise ValueError("Each driver variable must be an object")
        name = _required_name(spec.get("name"), "variable.name")
        if not _DRIVER_NAME_RE.fullmatch(name) or name == "frame":
            raise ValueError("Driver variable names must be identifiers other than 'frame'")
        if name in names:
            raise ValueError(f"Duplicate driver variable name: {name}")
        names.add(name)
        variable_type = str(spec.get("type", "")).upper()
        if variable_type not in _DRIVER_VARIABLE_TYPES:
            raise ValueError(f"Unsupported driver variable type: {variable_type}")
        source, source_type = _target(spec.get("target"))
        if variable_type == "SINGLE_PROP":
            source_path = _required_name(spec.get("data_path"), "variable.data_path")
            try:
                source.path_resolve(source_path)
            except Exception as exc:
                raise ValueError(f"Invalid source data_path {source_path!r}: {exc}") from exc
            prepared.append((name, variable_type, source, source_type, source_path, None, None, None))
        else:
            if source_type != "OBJECT":
                raise ValueError("TRANSFORMS variables require an OBJECT target")
            transform_type = str(spec.get("transform_type", "")).upper()
            transform_space = str(spec.get("transform_space", "WORLD_SPACE")).upper()
            if transform_type not in _DRIVER_TRANSFORM_TYPES:
                raise ValueError(f"Unsupported driver transform_type: {transform_type}")
            if transform_space not in _DRIVER_TRANSFORM_SPACES:
                raise ValueError(f"Unsupported driver transform_space: {transform_space}")
            bone_target = spec.get("bone_target") or ""
            if bone_target and (source.type != "ARMATURE" or source.data.bones.get(bone_target) is None):
                raise ValueError(f"Armature bone not found: {source.name}/{bone_target}")
            prepared.append(
                (name, variable_type, source, source_type, None, bone_target, transform_type, transform_space)
            )
    return prepared


def _replace_driver_variables(driver, prepared):
    while driver.variables:
        driver.variables.remove(driver.variables[0])
    for name, variable_type, source, source_type, data_path, bone_target, transform_type, transform_space in prepared:
        variable = driver.variables.new()
        variable.name = name
        variable.type = variable_type
        target = variable.targets[0]
        target.id_type = source_type
        target.id = source
        if variable_type == "SINGLE_PROP":
            target.data_path = data_path
        else:
            target.bone_target = bone_target
            target.transform_type = transform_type
            target.transform_space = transform_space


def _driver_result(owner, fcurve):
    driver = fcurve.driver
    return {
        "target": owner.name,
        "data_path": fcurve.data_path,
        "array_index": fcurve.array_index,
        "type": driver.type,
        "expression": driver.expression if driver.type == "SCRIPTED" else None,
        "mute": fcurve.mute,
        "variables": [variable.name for variable in driver.variables],
        "changed_resources": [owner.name],
    }


def _nla_track(data, name):
    return next((track for track in data.nla_tracks if track.name == name), None)


def _nla_strip(track, name):
    return next((strip for strip in track.strips if strip.name == name), None)


def _validate_nla_patch(patch, allowed, label):
    if not isinstance(patch, dict) or not patch:
        raise ValueError(f"{label} must be a non-empty object")
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise ValueError(f"Unsupported {label} settings: {unknown}")
    for name, value in patch.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    return patch


def _patch_nla(owner, patch, mapping=None):
    mapping = mapping or {}
    previous = {}
    try:
        for name, value in patch.items():
            property_name = mapping.get(name, name)
            previous[property_name] = getattr(owner, property_name)
            setattr(owner, property_name, value)
    except Exception:
        for name, value in previous.items():
            setattr(owner, name, value)
        raise
    return previous


class AnimationHandlersMixin:
    """Expose generic animation inspection, Actions, and keyframes."""

    def inspect_animation(self, target, offset=0, limit=200):
        owner, target_type = _target(target)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        data = _animation_data(owner)
        action = data.action if data else None
        slot = data.action_slot if data and action else None
        keys = _keyframe_info(action, slot.handle) if action and slot else []
        page = keys[offset : offset + limit]
        return {
            "target": {"type": target_type, "name": owner.name},
            "action": {
                "name": action.name,
                "users": action.users,
                "is_layered": action.is_action_layered,
                "slot": slot.identifier if slot else None,
                "slots": [
                    {"identifier": item.identifier, "target_id_type": item.target_id_type, "name": item.name_display}
                    for item in action.slots
                ],
                "frame_range": list(action.curve_frame_range),
            }
            if action
            else None,
            "keyframes": page,
            "total_keyframes": len(keys),
            "offset": offset,
            "limit": limit,
            "truncated": offset + len(page) < len(keys),
            "next_offset": offset + len(page) if offset + len(page) < len(keys) else None,
            "drivers": _driver_info(data),
            "nla_tracks": _nla_info(data),
        }

    def manage_animation_action(
        self,
        target,
        action,
        action_name=None,
        source_action_name=None,
        replace_active=False,
    ):
        owner, _target_type = _target(target)
        operation = str(action).upper()
        if operation not in {"CREATE", "ASSIGN", "DUPLICATE", "UNASSIGN"}:
            raise ValueError(f"Unsupported action operation: {operation}")
        data = _animation_data(owner, create=operation != "UNASSIGN")
        if operation == "UNASSIGN":
            if data is None or data.action is None:
                return {"target": owner.name, "action": None, "changed_resources": []}
            if action_name and data.action.name != action_name:
                raise ValueError(f"Active Action is {data.action.name}, not {action_name}")
            previous = data.action.name
            data.action = None
            return {"target": owner.name, "unassigned": previous, "changed_resources": [owner.name, previous]}

        action_name = _required_name(action_name, "action_name")
        if operation == "CREATE":
            if bpy.data.actions.get(action_name) is not None:
                raise ValueError(f"Action already exists: {action_name}")
            selected = bpy.data.actions.new(action_name)
        elif operation == "ASSIGN":
            selected = bpy.data.actions.get(action_name)
            if selected is None:
                raise ValueError(f"Action not found: {action_name}")
        else:
            source_name = _required_name(source_action_name, "source_action_name")
            source = bpy.data.actions.get(source_name)
            if source is None:
                raise ValueError(f"Source Action not found: {source_name}")
            if bpy.data.actions.get(action_name) is not None:
                raise ValueError(f"Action already exists: {action_name}")
            selected = source.copy()
            selected.name = action_name
        if not selected.is_action_layered:
            if operation in {"CREATE", "DUPLICATE"}:
                bpy.data.actions.remove(selected)
            raise ValueError(f"Action {selected.name} is legacy and cannot be assigned by this tool")
        try:
            slot = _assign_action(owner, selected, replace_active=replace_active)
        except Exception:
            if operation in {"CREATE", "DUPLICATE"}:
                bpy.data.actions.remove(selected)
            raise
        return {
            "target": owner.name,
            "action": selected.name,
            "slot": slot.identifier,
            "created": operation in {"CREATE", "DUPLICATE"},
            "changed_resources": [owner.name, selected.name],
        }

    def edit_keyframes(
        self,
        target,
        edits,
        action_name=None,
        replace_active_action=False,
        allow_shared_action=False,
    ):
        owner, _target_type = _target(target)
        if not isinstance(edits, list) or not 1 <= len(edits) <= 1000:
            raise ValueError("edits must contain between 1 and 1000 records")
        expanded = [item for edit in edits for item in _expanded_edit(owner, edit)]
        data = _animation_data(owner, create=True)
        current = data.action
        created_action = False
        if action_name:
            action_name = _required_name(action_name, "action_name")
            selected = bpy.data.actions.get(action_name)
            if selected is None:
                selected = bpy.data.actions.new(action_name)
                created_action = True
        elif current is not None:
            selected = current
        else:
            raise ValueError("Target has no active Action; provide action_name to create or assign one")
        other_users = selected.users - (1 if current == selected else 0)
        if other_users > 0 and not allow_shared_action:
            if created_action:
                bpy.data.actions.remove(selected)
            raise ValueError(
                f"Action {selected.name} has {selected.users} users; set allow_shared_action=True to edit it in place"
            )
        if not selected.is_action_layered:
            if created_action:
                bpy.data.actions.remove(selected)
            raise ValueError(f"Action {selected.name} is legacy; convert or duplicate it to a layered Action first")
        if action_name:
            try:
                slot = _assign_action(owner, selected, replace_active=replace_active_action)
            except Exception:
                if created_action:
                    bpy.data.actions.remove(selected)
                raise
        else:
            slot = _action_slot(selected, owner, create=True)
        bag = _channelbag(selected, slot, create=True)
        changed = []
        for operation, data_path, index, frame, value, interpolation, group in expanded:
            fcurve = bag.fcurves.find(data_path, index=index)
            key = _find_key(fcurve, frame) if fcurve else None
            if operation == "REMOVE":
                if key is None:
                    continue
                fcurve.keyframe_points.remove(key, fast=True)
                if not fcurve.keyframe_points:
                    bag.fcurves.remove(fcurve)
                else:
                    fcurve.update()
                changed.append({"operation": operation, "data_path": data_path, "array_index": index, "frame": frame})
                continue
            if fcurve is None:
                fcurve = bag.fcurves.new(data_path, index=index, group_name=group or "")
            if key is None:
                key = fcurve.keyframe_points.insert(frame, value, options={"FAST"})
            else:
                key.co[1] = value
            key.interpolation = interpolation
            fcurve.update()
            changed.append(
                {
                    "operation": operation,
                    "data_path": data_path,
                    "array_index": index,
                    "frame": frame,
                    "value": value,
                }
            )
        return {
            "target": owner.name,
            "action": selected.name,
            "slot": slot.identifier,
            "changed_keyframes": changed,
            "changed_resources": [owner.name, selected.name],
        }

    def manage_nla_tracks(
        self,
        target,
        action,
        track_name,
        strip_name=None,
        action_name=None,
        frame_start=None,
        track_patch=None,
        strip_patch=None,
        confirm_remove=False,
    ):
        owner, _target_type = _target(target)
        operation = str(action).upper()
        allowed = {"CREATE_TRACK", "ADD_STRIP", "PATCH_TRACK", "PATCH_STRIP", "REMOVE_STRIP", "REMOVE_TRACK"}
        if operation not in allowed:
            raise ValueError(f"Unsupported NLA operation: {operation}")
        track_name = _required_name(track_name, "track_name")
        data = _animation_data(owner, create=operation in {"CREATE_TRACK", "ADD_STRIP"})
        track = _nla_track(data, track_name) if data else None

        if operation == "CREATE_TRACK":
            if track is not None:
                raise ValueError(f"NLA track already exists: {track_name}")
            track = data.nla_tracks.new()
            track.name = track_name
            if track_patch:
                patch = _validate_nla_patch(track_patch, _NLA_TRACK_PROPERTIES, "track_patch")
                _patch_nla(track, patch, {"solo": "is_solo"})
            return {"target": owner.name, "track": track.name, "created": True, "changed_resources": [owner.name]}

        if track is None:
            raise ValueError(f"NLA track not found: {track_name}")
        if operation == "PATCH_TRACK":
            patch = _validate_nla_patch(track_patch, _NLA_TRACK_PROPERTIES, "track_patch")
            _patch_nla(track, patch, {"solo": "is_solo"})
        elif operation == "ADD_STRIP":
            strip_name = _required_name(strip_name, "strip_name")
            action_name = _required_name(action_name, "action_name")
            if _nla_strip(track, strip_name) is not None:
                raise ValueError(f"NLA strip already exists in {track_name}: {strip_name}")
            action_data = bpy.data.actions.get(action_name)
            if action_data is None:
                raise ValueError(f"Action not found: {action_name}")
            if not action_data.is_action_layered:
                raise ValueError(f"Action {action_name} is legacy and cannot be added by this tool")
            if isinstance(frame_start, bool) or not isinstance(frame_start, (int, float)) or not math.isfinite(frame_start):
                raise ValueError("frame_start must be a finite number")
            slot = _action_slot(action_data, owner, create=True)
            strip = track.strips.new(strip_name, math.floor(frame_start), action_data)
            try:
                strip.action_slot = slot
                strip.frame_start = float(frame_start)
                if strip_patch:
                    patch = _validate_nla_patch(strip_patch, _NLA_STRIP_PROPERTIES, "strip_patch")
                    resulting_start = patch.get("frame_start", strip.frame_start)
                    resulting_end = patch.get("frame_end", strip.frame_end)
                    if resulting_end <= resulting_start:
                        raise ValueError("Resulting frame_end must be greater than frame_start")
                    _patch_nla(strip, patch)
            except Exception:
                track.strips.remove(strip)
                raise
            return {
                "target": owner.name,
                "track": track.name,
                "strip": strip.name,
                "action": action_data.name,
                "changed_resources": [owner.name, action_data.name],
            }
        elif operation == "PATCH_STRIP":
            strip_name = _required_name(strip_name, "strip_name")
            strip = _nla_strip(track, strip_name)
            if strip is None:
                raise ValueError(f"NLA strip not found in {track_name}: {strip_name}")
            patch = _validate_nla_patch(strip_patch, _NLA_STRIP_PROPERTIES, "strip_patch")
            resulting_start = patch.get("frame_start", strip.frame_start)
            resulting_end = patch.get("frame_end", strip.frame_end)
            action_start = patch.get("action_frame_start", strip.action_frame_start)
            action_end = patch.get("action_frame_end", strip.action_frame_end)
            if resulting_end <= resulting_start:
                raise ValueError("Resulting frame_end must be greater than frame_start")
            if action_end <= action_start:
                raise ValueError("Resulting action_frame_end must be greater than action_frame_start")
            _patch_nla(strip, patch)
        elif operation == "REMOVE_STRIP":
            if not confirm_remove:
                raise ValueError("confirm_remove=True is required")
            strip_name = _required_name(strip_name, "strip_name")
            strip = _nla_strip(track, strip_name)
            if strip is None:
                raise ValueError(f"NLA strip not found in {track_name}: {strip_name}")
            track.strips.remove(strip)
            return {"target": owner.name, "track": track.name, "removed_strip": strip_name, "changed_resources": [owner.name]}
        else:
            if not confirm_remove:
                raise ValueError("confirm_remove=True is required")
            data.nla_tracks.remove(track)
            return {"target": owner.name, "removed_track": track_name, "changed_resources": [owner.name]}
        return {"target": owner.name, "track": _nla_info(data), "changed_resources": [owner.name]}

    def manage_animation_driver(
        self,
        target,
        action,
        data_path,
        array_index=-1,
        driver_type=None,
        expression=None,
        variables=None,
        mute=None,
        confirm_remove=False,
    ):
        owner, _target_type = _target(target)
        operation = str(action).upper()
        if operation not in {"ADD", "PATCH", "REMOVE"}:
            raise ValueError(f"Unsupported driver operation: {operation}")
        data_path = _required_name(data_path, "data_path")
        array_length, _value = _resolve_property(owner, data_path)
        if isinstance(array_index, bool) or not isinstance(array_index, int) or not -1 <= array_index <= 63:
            raise ValueError("array_index must be an integer from -1 to 63")
        if array_length:
            if array_index < 0 or array_index >= array_length:
                raise ValueError(f"array_index must select one channel of {data_path} (length {array_length})")
            normalized_index = array_index
        else:
            if array_index not in {-1, 0}:
                raise ValueError(f"array_index is invalid for scalar property {data_path}")
            normalized_index = 0
        fcurve = _driver_fcurve(owner, data_path, normalized_index)
        if operation == "REMOVE":
            if not confirm_remove:
                raise ValueError("confirm_remove=True is required")
            if fcurve is None:
                raise ValueError(f"Driver not found: {data_path}[{normalized_index}]")
            if not owner.driver_remove(data_path, normalized_index if array_length else -1):
                raise RuntimeError(f"Blender did not remove driver: {data_path}[{normalized_index}]")
            return {
                "target": owner.name,
                "removed": {"data_path": data_path, "array_index": normalized_index},
                "changed_resources": [owner.name],
            }
        if operation == "ADD" and fcurve is not None:
            raise ValueError(f"Driver already exists: {data_path}[{normalized_index}]")
        if operation == "PATCH" and fcurve is None:
            raise ValueError(f"Driver not found: {data_path}[{normalized_index}]")
        prepared = _prepare_driver_variables(variables) if variables is not None else None
        requested_type = str(driver_type).upper() if driver_type is not None else None
        if requested_type is not None and requested_type not in _DRIVER_TYPES:
            raise ValueError(f"Unsupported driver_type: {requested_type}")
        resulting_type = requested_type or fcurve.driver.type
        if expression is not None:
            if resulting_type != "SCRIPTED":
                raise ValueError("expression is valid only for a SCRIPTED driver")
            expression = _safe_expression(expression, {item[0] for item in prepared or []})
        if resulting_type == "SCRIPTED" and operation == "ADD" and expression is None:
            raise ValueError("SCRIPTED ADD requires expression")
        if operation == "ADD":
            try:
                fcurve = owner.driver_add(data_path, normalized_index if array_length else -1)
            except Exception as exc:
                raise ValueError(f"Could not add driver for {data_path}[{normalized_index}]: {exc}") from exc
        try:
            if requested_type is not None:
                fcurve.driver.type = requested_type
            if prepared is not None:
                _replace_driver_variables(fcurve.driver, prepared)
            if expression is not None:
                fcurve.driver.expression = expression
            if mute is not None:
                fcurve.mute = bool(mute)
        except Exception:
            if operation == "ADD":
                owner.driver_remove(data_path, normalized_index if array_length else -1)
            raise
        return _driver_result(owner, fcurve)
