import bpy

from ..helpers import _apply_modifier, _exit_edit_mode, _get_mesh_object, _mesh_counts, _select_geometry, _set_active


class ModelHandlersMixin:
    # region Model editing handlers
    def model_match_reference(
        self,
        object_name,
        reference_object_name,
        match_location=True,
        match_rotation=True,
        match_scale=True,
    ):
        """Align an object's transform to a reference object's transform."""
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")
        ref = bpy.data.objects.get(reference_object_name)
        if not ref:
            raise ValueError(f"Reference object not found: {reference_object_name}")
        if match_location:
            obj.location = ref.location.copy()
        if match_rotation:
            obj.rotation_euler = ref.rotation_euler.copy()
        if match_scale:
            obj.scale = ref.scale.copy()
        return {
            "name": obj.name,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
        }

    def model_blockout(
        self, name, primitive_type="CUBE", size=(1, 1, 1), location=(0, 0, 0)
    ):
        """Create a simple placeholder primitive scaled to size, tagged as a blockout proxy."""
        result = self.create_primitive(
            primitive_type=primitive_type, name=name, location=location
        )
        obj = bpy.data.objects[result["name"]]
        obj.scale = tuple(size)
        obj["blockout"] = True
        result["scale"] = [obj.scale.x, obj.scale.y, obj.scale.z]
        return result

    def model_refine(self, object_name, levels=1, apply=False):
        """Smooth and increase effective resolution via a Subdivision Surface modifier."""
        obj = _get_mesh_object(object_name)
        mod = obj.modifiers.new(name="Subdivision", type="SUBSURF")
        mod.levels = levels
        mod.render_levels = levels
        _set_active(obj)
        bpy.ops.object.shade_smooth()
        if apply:
            _apply_modifier(obj, mod)
        return {"name": obj.name, "applied": bool(apply), **_mesh_counts(obj)}

    def model_detail(
        self, object_name, strength=0.1, scale=5.0, texture_type="NOISE", apply=False
    ):
        """Add fine procedural surface detail via a Displace modifier driven by a noise/voronoi texture."""
        obj = _get_mesh_object(object_name)
        tex = bpy.data.textures.new(name=f"{object_name}_detail", type=texture_type)
        tex.noise_scale = scale
        mod = obj.modifiers.new(name="Displace", type="DISPLACE")
        mod.texture = tex
        mod.strength = strength
        if apply:
            _apply_modifier(obj, mod)
        return {"name": obj.name, "applied": bool(apply), **_mesh_counts(obj)}

    def model_symmetrize(self, object_name, direction="NEGATIVE_X_TO_POSITIVE_X"):
        """Symmetrize a mesh across an axis, mirroring one half onto the other."""
        obj = _get_mesh_object(object_name)
        _select_geometry(obj)
        bpy.ops.mesh.symmetrize(direction=direction)
        _exit_edit_mode()
        return {"name": obj.name, **_mesh_counts(obj)}

    def model_mirror(self, object_name, axis="X", merge=True, apply=False):
        """Add a Mirror modifier to an object across the given axis."""
        obj = _get_mesh_object(object_name)
        axis = str(axis).upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError(f"Invalid axis: {axis}. Must be one of X, Y, Z")
        mod = obj.modifiers.new(name="Mirror", type="MIRROR")
        mod.use_axis = [axis == a for a in ("X", "Y", "Z")]
        mod.use_clip = bool(merge)
        if apply:
            _apply_modifier(obj, mod)
        return {"name": obj.name, "applied": bool(apply), **_mesh_counts(obj)}

    def model_array(self, object_name, count=2, relative_offset=(1, 0, 0), apply=False):
        """Add a linear Array modifier to an object."""
        obj = _get_mesh_object(object_name)
        mod = obj.modifiers.new(name="Array", type="ARRAY")
        mod.count = count
        mod.relative_offset_displace = tuple(relative_offset)
        if apply:
            _apply_modifier(obj, mod)
        return {"name": obj.name, "applied": bool(apply), **_mesh_counts(obj)}

    def model_radial_array(self, object_name, count=6, axis="Z", apply=False):
        """Duplicate an object radially around its origin using an Array modifier driven by a helper empty."""
        obj = _get_mesh_object(object_name)
        axis = str(axis).upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError(f"Invalid axis: {axis}. Must be one of X, Y, Z")
        if count < 2:
            raise ValueError("count must be at least 2")
        empty = bpy.data.objects.new(f"{object_name}_radial_pivot", None)
        bpy.context.collection.objects.link(empty)
        empty.location = obj.location.copy()
        angle = (2 * 3.141592653589793) / count
        setattr(empty.rotation_euler, axis.lower(), angle)
        mod = obj.modifiers.new(name="Array", type="ARRAY")
        mod.count = count
        mod.use_relative_offset = False
        mod.use_object_offset = True
        mod.offset_object = empty
        if apply:
            _apply_modifier(obj, mod)
            bpy.data.objects.remove(empty, do_unlink=True)
        return {"name": obj.name, "applied": bool(apply), **_mesh_counts(obj)}

    # endregion
