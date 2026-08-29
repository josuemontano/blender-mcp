import bpy

from ..helpers import (
    _apply_modifier,
    _exit_edit_mode,
    _get_mesh_object,
    _mesh_counts,
    _select_geometry,
    _set_active,
)


class MeshHandlersMixin:
    # region Mesh editing handlers
    _PRIMITIVE_OPS = {
        "CUBE": lambda size, location, rotation: bpy.ops.mesh.primitive_cube_add(
            size=size, location=location, rotation=rotation
        ),
        "SPHERE": lambda size, location, rotation: bpy.ops.mesh.primitive_uv_sphere_add(
            radius=size, location=location, rotation=rotation
        ),
        "CYLINDER": lambda size, location, rotation: (
            bpy.ops.mesh.primitive_cylinder_add(
                radius=size, depth=size * 2, location=location, rotation=rotation
            )
        ),
        "CONE": lambda size, location, rotation: bpy.ops.mesh.primitive_cone_add(
            radius1=size, depth=size * 2, location=location, rotation=rotation
        ),
        "TORUS": lambda size, location, rotation: bpy.ops.mesh.primitive_torus_add(
            major_radius=size,
            minor_radius=size * 0.25,
            location=location,
            rotation=rotation,
        ),
        "PLANE": lambda size, location, rotation: bpy.ops.mesh.primitive_plane_add(
            size=size, location=location, rotation=rotation
        ),
        "CURVE": lambda size, location, rotation: (
            bpy.ops.curve.primitive_bezier_curve_add(
                radius=size, location=location, rotation=rotation
            )
        ),
    }

    def create_primitive(
        self,
        primitive_type,
        name=None,
        location=(0, 0, 0),
        rotation=(0, 0, 0),
        size=1.0,
    ):
        """Create a mesh/curve primitive: cube, sphere, cylinder, cone, torus, plane, or curve."""
        ptype = str(primitive_type).upper()
        op = self._PRIMITIVE_OPS.get(ptype)
        if not op:
            raise ValueError(
                f"Unknown primitive_type: {primitive_type}. Must be one of {sorted(self._PRIMITIVE_OPS)}"
            )
        op(size, tuple(location), tuple(rotation))
        obj = bpy.context.active_object
        if name:
            obj.name = name
        result = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
        }
        if obj.type == "MESH":
            result.update(_mesh_counts(obj))
        return result

    def mesh_extrude(self, object_name, offset=(0, 0, 1), face_indices=None):
        """Extrude the selected (or all) faces of a mesh by offset."""
        obj = _get_mesh_object(object_name)
        _select_geometry(obj, face_indices=face_indices)
        bpy.ops.mesh.extrude_region_move(
            TRANSFORM_OT_translate={"value": tuple(offset)}
        )
        _exit_edit_mode()
        return {"name": obj.name, **_mesh_counts(obj)}

    def mesh_inset(self, object_name, thickness=0.05, depth=0.0, face_indices=None):
        """Inset the selected (or all) faces of a mesh."""
        obj = _get_mesh_object(object_name)
        _select_geometry(obj, face_indices=face_indices)
        bpy.ops.mesh.inset_faces(thickness=thickness, depth=depth)
        _exit_edit_mode()
        return {"name": obj.name, **_mesh_counts(obj)}

    def mesh_bevel(
        self,
        object_name,
        offset=0.05,
        segments=1,
        affect="EDGES",
        edge_indices=None,
        vertex_indices=None,
    ):
        """Bevel the selected (or all) edges/vertices of a mesh."""
        obj = _get_mesh_object(object_name)
        _select_geometry(obj, vert_indices=vertex_indices, edge_indices=edge_indices)
        bpy.ops.mesh.bevel(offset=offset, segments=segments, affect=affect)
        _exit_edit_mode()
        return {"name": obj.name, **_mesh_counts(obj)}

    def mesh_bridge(self, object_name, edge_indices):
        """Bridge two selected open edge loops of a mesh."""
        if not edge_indices:
            raise ValueError(
                "edge_indices is required: select the edges forming the two loops to bridge"
            )
        obj = _get_mesh_object(object_name)
        _select_geometry(obj, edge_indices=edge_indices)
        bpy.ops.mesh.bridge_edge_loops()
        _exit_edit_mode()
        return {"name": obj.name, **_mesh_counts(obj)}

    def mesh_boolean(
        self, object_name, cutter_object_name, operation="DIFFERENCE", keep_target=False
    ):
        """Apply a boolean modifier between two mesh objects, deleting the cutter unless keep_target."""
        operation = str(operation).upper()
        if operation not in {"UNION", "DIFFERENCE", "INTERSECT"}:
            raise ValueError(
                f"Invalid operation: {operation}. Must be one of UNION, DIFFERENCE, INTERSECT"
            )
        obj = _get_mesh_object(object_name)
        cutter = _get_mesh_object(cutter_object_name)
        mod = obj.modifiers.new(name="Boolean", type="BOOLEAN")
        mod.object = cutter
        mod.operation = operation
        _apply_modifier(obj, mod)
        if not keep_target:
            bpy.data.objects.remove(cutter, do_unlink=True)
        return {"name": obj.name, **_mesh_counts(obj)}

    def mesh_subdivide(self, object_name, cuts=1, face_indices=None):
        """Subdivide the selected (or all) faces of a mesh."""
        obj = _get_mesh_object(object_name)
        _select_geometry(obj, face_indices=face_indices)
        bpy.ops.mesh.subdivide(number_cuts=cuts)
        _exit_edit_mode()
        return {"name": obj.name, **_mesh_counts(obj)}

    def mesh_remesh(self, object_name, voxel_size=0.1):
        """Voxel-remesh a mesh object, rebuilding its topology at the given voxel size."""
        obj = _get_mesh_object(object_name)
        obj.data.remesh_voxel_size = voxel_size
        _set_active(obj)
        bpy.ops.object.voxel_remesh()
        return {"name": obj.name, **_mesh_counts(obj)}

    def mesh_solidify(self, object_name, thickness=0.01, apply=True):
        """Add thickness to a mesh's surface via a Solidify modifier."""
        obj = _get_mesh_object(object_name)
        mod = obj.modifiers.new(name="Solidify", type="SOLIDIFY")
        mod.thickness = thickness
        if apply:
            _apply_modifier(obj, mod)
        return {"name": obj.name, "applied": bool(apply), **_mesh_counts(obj)}

    # endregion
