import bpy

from ..helpers import (
    _exit_edit_mode,
    _find_view3d,
    _get_mesh_object,
    _mesh_counts,
    _nd_call,
    _nd_configure_object_as_util,
    _select_objects,
)


class NDHandlersMixin:
    # region ND (HugeMenace) non-destructive workflow tools
    def nd_boolean(self, object_name, cutter_object_name, mode="DIFFERENCE"):
        """ND non-destructive boolean: live Boolean modifier on object_name, cutter_object_name becomes a wireframe utility parented to it."""
        mode = str(mode).upper()
        if mode not in {"UNION", "DIFFERENCE", "INTERSECT"}:
            raise ValueError(
                f"Invalid mode: {mode}. Must be one of UNION, DIFFERENCE, INTERSECT"
            )
        target = _get_mesh_object(object_name)
        cutter = _get_mesh_object(cutter_object_name)
        _select_objects([cutter.name, target.name], active_name=target.name)
        # execute() reads attributes bool_vanilla only sets inside invoke(); INVOKE_DEFAULT's
        # synthetic event always has shift/alt False, so this always converts+cleans the cutter.
        _nd_call("bool_vanilla", bpy.ops.nd.bool_vanilla, "INVOKE_DEFAULT", mode=mode)
        return {"name": target.name, "cutter_name": cutter.name, **_mesh_counts(target)}

    def nd_mark_as_util(self, object_names, unmark=False):
        """Mark/unmark objects as ND utility objects (wireframe display, hidden from render)."""
        if not object_names:
            raise ValueError("At least one object name is required")
        names = []
        for name in object_names:
            obj = bpy.data.objects.get(name)
            if not obj:
                raise ValueError(f"Object not found: {name}")
            _nd_configure_object_as_util(obj, util=not unmark)
            names.append(obj.name)
        return {"names": names, "marked_as_util": not unmark}

    def nd_clean_utils(self):
        """Remove orphaned boolean/array/mirror/lattice modifiers and their ND utility objects, scene-wide."""
        _nd_call("clean_utils", bpy.ops.nd.clean_utils, "INVOKE_DEFAULT")
        return {"status": "cleaned"}

    def nd_create_id_material(self, object_names, material_name):
        """Create/assign an ND ID material to the given mesh/curve objects."""
        objs = _select_objects(object_names)
        _nd_call(
            "create_id_material",
            bpy.ops.nd.create_id_material,
            material_name=material_name,
        )
        return {"names": [obj.name for obj in objs], "material_name": material_name}

    def nd_bulk_create_id_materials(self, object_names):
        """Assign a random distinct ND ID material to each given mesh/curve object."""
        objs = _select_objects(object_names)
        _nd_call("bulk_create_id_materials", bpy.ops.nd.bulk_create_id_materials)
        return {"names": [obj.name for obj in objs]}

    def nd_clear_materials(self, object_names):
        """Remove all material slots from the given mesh/curve objects."""
        objs = _select_objects(object_names)
        _nd_call("clear_materials", bpy.ops.nd.clear_materials)
        return {"names": [obj.name for obj in objs]}

    def nd_set_lod_suffix(self, object_names, mode="HIGH"):
        """Suffix object (and data) names with _high or _low, replacing any existing LOD suffix."""
        mode = str(mode).upper()
        if mode not in {"HIGH", "LOW"}:
            raise ValueError(f"Invalid mode: {mode}. Must be one of HIGH, LOW")
        objs = _select_objects(object_names)
        _nd_call("set_lod_suffix", bpy.ops.nd.set_lod_suffix, mode=mode)
        return {"names": [obj.name for obj in objs]}

    def nd_name_sync(self, object_names):
        """Sync each object's data-block name to match its object name."""
        objs = _select_objects(object_names)
        _nd_call("name_sync", bpy.ops.nd.name_sync)
        return {"names": [obj.name for obj in objs]}

    def nd_single_vertex(self, location=(0, 0, 0)):
        """Create an ND single-vertex sketch object at location, left in Object mode."""
        prev_cursor = tuple(bpy.context.scene.cursor.location)
        bpy.context.scene.cursor.location = tuple(location)
        try:
            _nd_call("single_vertex", bpy.ops.nd.single_vertex)
        finally:
            bpy.context.scene.cursor.location = prev_cursor
        obj = bpy.context.view_layer.objects.active
        _exit_edit_mode()
        return {
            "name": obj.name,
            "location": [obj.location.x, obj.location.y, obj.location.z],
        }

    def nd_clear_edge_marks(self, object_name):
        """Remove sharp/seam/freestyle edge marks from a mesh object."""
        obj = _get_mesh_object(object_name)
        _select_objects([obj.name])
        _nd_call("clear_edge_marks", bpy.ops.nd.clear_edge_marks)
        return {"name": obj.name}

    def nd_clear_vertex_groups(self, object_name):
        """Remove all vertex groups from a mesh object."""
        obj = _get_mesh_object(object_name)
        _select_objects([obj.name])
        _nd_call("clear_vertex_groups", bpy.ops.nd.clear_vertex_groups)
        return {"name": obj.name}

    def nd_apply_modifiers(self, object_names):
        """Apply modifiers on the given objects via ND (always REGULAR mode - SOFT/HARD/duplicate need real modifier keys, unreachable from a script)."""
        objs = _select_objects(object_names)
        _nd_call("apply_modifiers", bpy.ops.nd.apply_modifiers, "INVOKE_DEFAULT")
        return {"names": [obj.name for obj in objs]}

    def nd_viewport_toggle(self, toggle):
        """Toggle an ND viewport display setting: CAVITY, WIREFRAMES, FACE_ORIENTATION, CLEAR_VIEW, CUSTOM_VIEW, or UTILS.

        ND's SILHOUETTE toggle is a genuine modal operator and is intentionally not exposed here.
        """
        toggle = str(toggle).upper()
        op_by_toggle = {
            "CAVITY": bpy.ops.nd.toggle_cavity,
            "WIREFRAMES": bpy.ops.nd.toggle_wireframes,
            "FACE_ORIENTATION": bpy.ops.nd.toggle_face_orientation,
            "CLEAR_VIEW": bpy.ops.nd.toggle_clear_view,
            "CUSTOM_VIEW": bpy.ops.nd.toggle_custom_view,
            "UTILS": bpy.ops.nd.toggle_utils,
        }
        op = op_by_toggle.get(toggle)
        if op is None:
            raise ValueError(
                f"Invalid toggle: {toggle}. Must be one of {', '.join(op_by_toggle)}"
            )
        op_name = f"toggle_{toggle.lower()}"
        if toggle == "UTILS":
            _nd_call(op_name, op)
        else:
            # These toggles read bpy.context.space_data directly, which is None outside
            # an actual VIEW_3D UI region - override it to a real viewport's area/region.
            area, region = _find_view3d()
            if area is None:
                raise RuntimeError("No 3D viewport found to toggle")
            with bpy.context.temp_override(area=area, region=region):
                _nd_call(op_name, op)
        return {"toggle": toggle}

    def nd_capture_utils(self):
        """Display and select all ND utility objects in the scene."""
        _nd_call("capture_utils", bpy.ops.nd.capture_utils, "INVOKE_DEFAULT")
        return {"status": "captured"}

    def get_nd_status(self):
        """Get the current status of the ND (HugeMenace) non-destructive workflow integration"""
        enabled = bpy.context.scene.blendermcp_use_nd
        nd_installed = hasattr(bpy.ops, "nd") and hasattr(bpy.ops.nd, "bool_vanilla")
        if not enabled:
            return {
                "enabled": False,
                "message": """ND integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use ND (non-destructive hard-surface tools)' checkbox
                            3. Restart the connection to Claude""",
            }
        if not nd_installed:
            return {
                "enabled": False,
                "message": "ND integration is enabled in BlenderMCP, but the ND addon "
                "(https://extensions.blender.org/add-ons/nd/) does not appear to be "
                "installed/enabled in this Blender instance.",
            }
        return {
            "enabled": True,
            "message": "ND integration is enabled and the ND addon is installed and ready to use.",
        }

    # endregion
