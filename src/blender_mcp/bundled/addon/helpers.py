import bmesh
import bpy

from . import ADDON_ID


def get_blendermcp_addon_preferences(context=None):
    """Get add-on preferences object if available."""
    if context is None:
        context = bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


# region Mesh/model editing helpers
def _get_mesh_object(name):
    """Look up an object by name and require it to be a mesh."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise ValueError(f"Object not found: {name}")
    if obj.type != "MESH":
        raise ValueError(f"Object '{name}' is not a mesh (type={obj.type})")
    return obj


def _set_active(obj):
    """Make obj the sole selected + active object."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _select_geometry(obj, vert_indices=None, edge_indices=None, face_indices=None):
    """Enter edit mode on obj and select the given indices, or everything if none given."""
    _set_active(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    any_indices = bool(vert_indices or edge_indices or face_indices)
    for v in bm.verts:
        v.select = not any_indices
    for e in bm.edges:
        e.select = not any_indices
    for f in bm.faces:
        f.select = not any_indices
    if vert_indices:
        for i in vert_indices:
            bm.verts[i].select = True
    if edge_indices:
        for i in edge_indices:
            bm.edges[i].select = True
    if face_indices:
        for i in face_indices:
            bm.faces[i].select = True
    bm.select_flush(True)
    bmesh.update_edit_mesh(obj.data)


def _exit_edit_mode():
    bpy.ops.object.mode_set(mode="OBJECT")


def _mesh_counts(obj):
    return {
        "vertices": len(obj.data.vertices),
        "edges": len(obj.data.edges),
        "polygons": len(obj.data.polygons),
    }


def _apply_modifier(obj, modifier):
    _set_active(obj)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def _select_objects(names, active_name=None):
    """Deselect everything, select the named objects, and set the active object."""
    if not names:
        raise ValueError("At least one object name is required")
    objs = []
    for name in names:
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        objs.append(obj)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    active = bpy.data.objects.get(active_name) if active_name else objs[-1]
    bpy.context.view_layer.objects.active = active
    return objs


def _find_view3d():
    """Locate a VIEW_3D area/region, needed to override bpy.context.space_data for ND's viewport operators."""
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                return area, region
    return None, None


def _nd_call(op_name, op, *args, **kwargs):
    """Call an ND operator, raising if it unexpectedly enters a modal state."""
    result = op(*args, **kwargs)
    if "RUNNING_MODAL" in result:
        raise RuntimeError(
            f"nd.{op_name} entered a modal state unexpectedly - not safe to call headlessly"
        )
    return result


def _nd_configure_object_as_util(obj, util=True):
    """Replicate ND's lib/objects.configure_object_as_util (mark/unmark a utility object)."""
    obj.display_type = "WIRE" if util else "SOLID"
    obj.hide_render = util
    obj.visible_camera = not util
    obj.visible_diffuse = not util
    obj.visible_glossy = not util
    obj.visible_shadow = not util
    obj.visible_transmission = not util
    obj.visible_volume_scatter = not util


# endregion
