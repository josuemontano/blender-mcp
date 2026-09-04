import math

import bpy
import mathutils

from ..helpers import (
    apply_modifier,
    get_mesh_object,
    get_rotation_quaternion,
    modifier_result,
    pivot_rotation_matrix,
    preserve_mode_and_selection,
    rotation_as_native_list,
    set_active,
    set_rotation_quaternion,
)
from .geometry_nodes import GeometryNodesHandlersMixin

_SPACES = {"LOCAL", "WORLD"}


class ModelHandlersMixin(GeometryNodesHandlersMixin):
    """Provide handlers for modifying existing scene models."""

    # region Model editing handlers
    def copy_object_transform(
        self,
        object_name,
        reference_object_name,
        match_location=True,
        match_rotation=True,
        match_scale=True,
        space="WORLD",
    ):
        """
        Align an object's transform to a reference object's transform.

        space="WORLD" (default) matches visually across differently-parented
        objects by decomposing/recomposing matrix_world. space="LOCAL" copies
        the raw local location/rotation/scale properties instead.

        Args:
            object_name: Name of the Blender object to operate on.
            reference_object_name: Name of the reference object.
            match_location: Value for match location.
            match_rotation: Value for match rotation.
            match_scale: Value for match scale.
            space: Value for space.

        Returns:
            location/rotation/scale: obj's local properties after the match
                (rotation in obj's own rotation_mode representation, regardless
                of which space was used to perform the match).
            world_location/world_rotation_quaternion/world_scale: obj's
                matrix_world decomposed - the world-space equivalents, with
                rotation always as a quaternion.

        Raises:
            ValueError: If the operation cannot be completed.

        """
        space = str(space).upper()
        if space not in _SPACES:
            raise ValueError(f"Invalid space: {space}. Must be one of {sorted(_SPACES)}")
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")
        ref = bpy.data.objects.get(reference_object_name)
        if not ref:
            raise ValueError(f"Reference object not found: {reference_object_name}")

        if space == "LOCAL":
            if match_location:
                obj.location = ref.location.copy()
            if match_rotation:
                set_rotation_quaternion(obj, get_rotation_quaternion(ref))
            if match_scale:
                obj.scale = ref.scale.copy()
        else:
            ref_loc, ref_rot, ref_scale = ref.matrix_world.decompose()
            obj_loc, obj_rot, obj_scale = obj.matrix_world.decompose()
            new_loc = ref_loc if match_location else obj_loc
            new_rot = ref_rot if match_rotation else obj_rot
            new_scale = ref_scale if match_scale else obj_scale
            obj.matrix_world = mathutils.Matrix.LocRotScale(new_loc, new_rot, new_scale)

        world_loc, world_rot, world_scale = obj.matrix_world.decompose()
        return {
            "name": obj.name,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": rotation_as_native_list(obj),
            "rotation_mode": obj.rotation_mode,
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "world_location": [world_loc.x, world_loc.y, world_loc.z],
            "world_rotation_quaternion": [world_rot.w, world_rot.x, world_rot.y, world_rot.z],
            "world_scale": [world_scale.x, world_scale.y, world_scale.z],
        }

    def add_subdivision_surface_modifier(self, object_name, levels=1, apply=False):
        """
        Smooth and increase effective resolution via a Subdivision Surface modifier.

        Args:
            object_name: Name of the Blender object to operate on.
            levels: Value for levels.
            apply: Value for apply.

        Returns:
            Result produced by the operation.

        """
        obj = get_mesh_object(object_name)
        mod = obj.modifiers.new(name="Subdivision", type="SUBSURF")
        mod.levels = levels
        mod.render_levels = levels
        with preserve_mode_and_selection():
            set_active(obj)
            bpy.ops.object.shade_smooth()
        if apply:
            apply_modifier(obj, mod)
        return {"name": obj.name, **modifier_result(obj, mod, apply)}

    def add_displace_modifier(
        self,
        object_name,
        strength=0.1,
        scale=5.0,
        texture_type="NOISE",
        apply=False,
        subdivide=False,
    ):
        """
        Add fine procedural surface detail via a Displace modifier driven by a noise/voronoi texture.

        Displace only offsets existing vertices - it cannot create fine detail
        on a mesh that doesn't already have enough topology. Set subdivide=True
        to add a Subdivision Surface modifier first, or subdivide the mesh
        yourself before calling this. The Subdivision modifier is only baked in
        (applied) when apply=True as well - with apply=False both modifiers are
        left live so the result stays fully non-destructive.

        Args:
            object_name: Name of the Blender object to operate on.
            strength: Value for strength.
            scale: Value for scale.
            texture_type: Value for texture type.
            apply: Value for apply.
            subdivide: Value for subdivide.

        Returns:
            Result produced by the operation.

        """
        obj = get_mesh_object(object_name)
        if subdivide:
            subsurf = obj.modifiers.new(name="Subdivision", type="SUBSURF")
            subsurf.levels = 2
            subsurf.render_levels = 2
            if apply:
                apply_modifier(obj, subsurf)
        tex = bpy.data.textures.new(name=f"{object_name}_detail", type=texture_type)
        tex.noise_scale = scale
        mod = obj.modifiers.new(name="Displace", type="DISPLACE")
        mod.texture = tex
        mod.strength = strength
        if apply:
            apply_modifier(obj, mod)
            bpy.data.textures.remove(tex, do_unlink=True)
        return {"name": obj.name, **modifier_result(obj, mod, apply)}

    def model_mirror(self, object_name, axis="X", merge=True, clip=True, apply=False):
        """
        Add a Mirror modifier to an object across the given axis.

        Args:
            object_name: Name of the Blender object to operate on.
            axis: Axis that controls the operation.
            merge: Value for merge.
            clip: Value for clip.
            apply: Value for apply.

        Returns:
            Result produced by the operation.

        Raises:
            ValueError: If the operation cannot be completed.

        """
        obj = get_mesh_object(object_name)
        axis = str(axis).upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError(f"Invalid axis: {axis}. Must be one of X, Y, Z")
        mod = obj.modifiers.new(name="Mirror", type="MIRROR")
        mod.use_axis = [axis == a for a in ("X", "Y", "Z")]
        mod.use_mirror_merge = bool(merge)
        mod.use_clip = bool(clip)
        if apply:
            apply_modifier(obj, mod)
        return {"name": obj.name, **modifier_result(obj, mod, apply)}

    def model_array(self, object_name, count=2, relative_offset=(1, 0, 0), apply=False):
        """
        Add a linear Array modifier to an object.

        Args:
            object_name: Name of the Blender object to operate on.
            count: Value for count.
            relative_offset: Value for relative offset.
            apply: Value for apply.

        Returns:
            Result produced by the operation.

        """
        obj = get_mesh_object(object_name)
        mod = obj.modifiers.new(name="Array", type="ARRAY")
        mod.count = count
        mod.relative_offset_displace = tuple(relative_offset)
        if apply:
            apply_modifier(obj, mod)
        return {"name": obj.name, **modifier_result(obj, mod, apply)}

    _RADIAL_AXIS_PERP = {"X": "Y", "Y": "Z", "Z": "X"}

    def model_radial_array(
        self,
        object_name,
        count=6,
        axis="Z",
        apply=False,
        pivot_object_name=None,
        pivot_location=None,
        radius=None,
    ):
        """
        Duplicate an object radially around a pivot using an Array modifier driven by a helper empty.

        The array's visible spread is the distance between the object and the
        pivot - if the mesh is centered on its own origin, every rotated copy
        lands on top of the original. Provide one of pivot_object_name,
        pivot_location, or radius to set that distance; omitting all three
        raises an error instead of silently producing overlapping copies.

        Args:
            object_name: Name of the Blender object to operate on.
            count: Value for count.
            axis: Axis that controls the operation.
            apply: Value for apply.
            pivot_object_name: Name of the pivot object.
            pivot_location: Value for pivot location.
            radius: Value for radius.

        Returns:
            Result produced by the operation.

        Raises:
            ValueError: If the operation cannot be completed.

        """
        obj = get_mesh_object(object_name)
        axis = str(axis).upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError(f"Invalid axis: {axis}. Must be one of X, Y, Z")
        if count < 2:
            raise ValueError("count must be at least 2")
        if sum(p is not None for p in (pivot_object_name, pivot_location, radius)) > 1:
            raise ValueError("Provide at most one of pivot_object_name, pivot_location, or radius")

        if pivot_object_name:
            pivot_obj = bpy.data.objects.get(pivot_object_name)
            if not pivot_obj:
                raise ValueError(f"Pivot object not found: {pivot_object_name}")
            pivot_loc = pivot_obj.matrix_world.translation.copy()
        elif pivot_location:
            pivot_loc = mathutils.Vector(pivot_location)
        elif radius:
            perp = self._RADIAL_AXIS_PERP[axis].lower()
            pivot_loc = obj.matrix_world.translation.copy()
            setattr(pivot_loc, perp, getattr(pivot_loc, perp) - radius)
        else:
            raise ValueError(
                "model_radial_array needs a pivot offset from the object's own "
                "location or every copy will overlap - pass pivot_object_name, "
                "pivot_location, or radius"
            )

        empty = bpy.data.objects.new(f"{object_name}_radial_pivot", None)
        bpy.context.collection.objects.link(empty)
        angle = (2 * math.pi) / count
        empty.matrix_world = pivot_rotation_matrix(pivot_loc, axis, angle) @ obj.matrix_world
        mod = obj.modifiers.new(name="Array", type="ARRAY")
        mod.count = count
        mod.use_relative_offset = False
        mod.use_object_offset = True
        mod.offset_object = empty
        if apply:
            apply_modifier(obj, mod)
            bpy.data.objects.remove(empty, do_unlink=True)
        return {"name": obj.name, **modifier_result(obj, mod, apply)}

    def sync_data_name(self, object_names):
        """
        Sync each object's data-block name to match its object name.

        Args:
            object_names: Names of Blender objects to operate on.

        Returns:
            Result produced by the operation.

        Raises:
            ValueError: If the operation cannot be completed.

        """
        if not object_names:
            raise ValueError("At least one object name is required")
        objs = []
        for name in object_names:
            obj = bpy.data.objects.get(name)
            if not obj:
                raise ValueError(f"Object not found: {name}")
            objs.append(obj)
        for obj in objs:
            if obj.data is not None:
                obj.data.name = obj.name
        return {"names": [obj.name for obj in objs]}

    # endregion
