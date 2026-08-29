# pyright: reportArgumentType=false, reportGeneralTypeIssues=false, reportOptionalSubscript=false
"""Blender-main-thread handlers for guided retopology and asset handoff."""

import contextlib
import math
import os
import statistics

from pathlib import Path

import bmesh
import bpy
import mathutils

from ..helpers import apply_modifier, edit_mesh, get_mesh_object, mesh_counts, preserve_mode_and_selection
from .retopology import (
    RetopologyHandlersMixin,
    _bvh_class,
    _editable_bmesh,
    _ensure_indices,
    _finite,
    _modifier_order,
    _nearest_projection,
    _positive,
    _project_vertices,
    _read_bmesh,
    _require_revision,
    _vector,
    _world_bvh,
    topology_revision,
)

_GUIDE_ROLES = {
    "EYE_LOOP",
    "MOUTH_LOOP",
    "JOINT_RING",
    "HARD_EDGE",
    "SEAM",
    "DENSITY_TRANSITION",
    "PANEL_BOUNDARY",
    "CUSTOM",
}
_TRANSFER_TYPES = {
    "VERTEX_GROUPS",
    "UVS",
    "COLOR_ATTRIBUTES",
    "CUSTOM_NORMALS",
    "SEAMS",
    "CREASES",
    "BEVEL_WEIGHTS",
    "SHARP_EDGES",
    "SMOOTH_SHADING",
    "MATERIAL_INDICES",
}


def _named_collection(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("collection_name must be a non-empty string")
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def _require_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _require_finished(result, operation):
    if not result or "FINISHED" not in result:
        raise RuntimeError(f"{operation} did not finish (operator returned {sorted(result or [])})")


def _curve_object(name, collection, points, cyclic, properties):
    curve = bpy.data.curves.new(name=f"{name}_Curve", type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for item, coordinate in zip(spline.points, points, strict=True):
        item.co = (*coordinate, 1.0)
    spline.use_cyclic_u = bool(cyclic)
    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    for key, value in properties.items():
        obj[key] = value
    return obj


def _project_world_points(source, points, offset, max_distance=None):
    tree = _world_bvh(source)
    projected = []
    for index, point in enumerate(points):
        hit = _nearest_projection(tree, point, offset, max_distance)
        if hit is None:
            raise ValueError(f"Point {index} did not project within max_projection_distance")
        projected.append(hit[0])
    return projected


def _polyline_length(points, cyclic):
    pairs = list(zip(points, points[1:], strict=False))
    if cyclic and len(points) > 1:
        pairs.append((points[-1], points[0]))
    return sum((second - first).length for first, second in pairs)


def _resample_polyline(points, count, cyclic):
    if len(points) < 2:
        raise ValueError("A section component must contain at least two distinct points")
    segment_count = len(points) if cyclic else len(points) - 1
    lengths = [(points[(index + 1) % len(points)] - points[index]).length for index in range(segment_count)]
    total = sum(lengths)
    if total <= 1e-12:
        raise ValueError("Cannot resample a zero-length section")
    targets = [total * index / (count if cyclic else count - 1) for index in range(count)]
    result = []
    for target in targets:
        walked = 0.0
        for index, length in enumerate(lengths):
            if walked + length >= target or index == len(lengths) - 1:
                factor = 0.0 if length <= 1e-12 else min(1.0, (target - walked) / length)
                result.append(points[index].lerp(points[(index + 1) % len(points)], factor))
                break
            walked += length
    return result


def _ordered_edge_components(edges):
    remaining = set(edges)
    components = []
    while remaining:
        seed = min(remaining, key=lambda edge: edge.index)
        component = set()
        queue = [seed]
        while queue:
            edge = queue.pop()
            if edge in component:
                continue
            component.add(edge)
            queue.extend(
                linked
                for vertex in edge.verts
                for linked in vertex.link_edges
                if linked in remaining and linked not in component
            )
        remaining -= component
        degrees = {}
        for edge in component:
            for vertex in edge.verts:
                degrees[vertex] = degrees.get(vertex, 0) + 1
        if any(degree > 2 for degree in degrees.values()):
            continue
        endpoints = [vertex for vertex, degree in degrees.items() if degree == 1]
        cyclic = not endpoints
        current = min(endpoints or degrees, key=lambda vertex: vertex.index)
        start = current
        ordered = [current.co.copy()]
        unused = set(component)
        while unused:
            candidates = [edge for edge in current.link_edges if edge in unused]
            if not candidates:
                break
            edge = min(candidates, key=lambda candidate: candidate.index)
            unused.remove(edge)
            current = edge.other_vert(current)
            if current is not start:
                ordered.append(current.co.copy())
        components.append((ordered, cyclic))
    return components


def _mesh_attribute(mesh, name, data_type):
    attribute = mesh.attributes.get(name)
    if attribute is not None and (attribute.domain != "EDGE" or attribute.data_type != data_type):
        raise ValueError(f"Mesh attribute '{name}' exists but is not an EDGE/{data_type} attribute")
    return attribute or mesh.attributes.new(name=name, type=data_type, domain="EDGE")


def _curve_world_points(obj):
    if obj.type != "CURVE":
        raise ValueError(f"Guide object '{obj.name}' is not a Curve")
    result = []
    for spline in obj.data.splines:
        if spline.type == "BEZIER":
            result.extend(obj.matrix_world @ point.co for point in spline.bezier_points)
        else:
            result.extend(obj.matrix_world @ point.co.xyz for point in spline.points)
    return result


def _evaluated_mesh_snapshot(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        edges = [tuple(edge.vertices) for edge in mesh.edges]
        polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
        return vertices, edges, polygons
    finally:
        evaluated.to_mesh_clear()


def _surface_statistics(vertices, edges, polygons):
    edge_lengths = [(vertices[second] - vertices[first]).length for first, second in edges]
    areas, normals = [], []
    volume = 0.0
    for polygon in polygons:
        points = [vertices[index] for index in polygon]
        if len(points) < 3:
            areas.append(0.0)
            normals.append(mathutils.Vector((0.0, 0.0, 0.0)))
            continue
        normal = mathutils.geometry.normal(points)
        area = sum(
            mathutils.geometry.area_tri(points[0], points[index], points[index + 1])
            for index in range(1, len(points) - 1)
        )
        areas.append(area)
        normals.append(normal)
        volume += sum(
            points[0].dot(points[index].cross(points[index + 1])) / 6.0 for index in range(1, len(points) - 1)
        )
    return edge_lengths, areas, normals, volume


class RetopologyPhaseOneHandlersMixin:
    """Provide Phase 1 guided construction and asset-handoff handlers."""

    def create_retopology_guides(
        self,
        source_object_name,
        guides,
        collection_name="Retopology Guides",
        projection_offset=0.0,
        max_projection_distance=None,
    ):
        source = get_mesh_object(source_object_name)
        offset = _finite(projection_offset, "projection_offset")
        max_distance = (
            _positive(max_projection_distance, "max_projection_distance")
            if max_projection_distance is not None
            else None
        )
        if not isinstance(guides, list) or not guides:
            raise ValueError("guides must be a non-empty list")
        prepared = []
        for guide_index, guide in enumerate(guides):
            if not isinstance(guide, dict):
                raise ValueError(f"guides[{guide_index}] must be an object")
            name = _require_name(guide.get("name"), f"guides[{guide_index}].name")
            role = str(guide.get("role", "")).upper()
            if role not in _GUIDE_ROLES:
                raise ValueError(f"guides[{guide_index}].role must be one of {sorted(_GUIDE_ROLES)}")
            has_points = guide.get("points") is not None
            has_indices = guide.get("source_vertex_indices") is not None
            if has_points == has_indices:
                raise ValueError(f"guides[{guide_index}] needs exactly one of points or source_vertex_indices")
            if has_points:
                raw_points = guide["points"]
                if not isinstance(raw_points, list) or len(raw_points) < 2:
                    raise ValueError(f"guides[{guide_index}].points needs at least two world-space points")
                points = [_vector(point, f"guides[{guide_index}].points") for point in raw_points]
            else:
                indices = _ensure_indices(
                    source.data.vertices,
                    guide["source_vertex_indices"],
                    f"guides[{guide_index}].source_vertex_indices",
                    required=True,
                )
                if len(indices) < 2:
                    raise ValueError(f"guides[{guide_index}].source_vertex_indices needs at least two vertices")
                points = [source.matrix_world @ source.data.vertices[index].co for index in indices]
            prepared.append(
                (name, role, _project_world_points(source, points, offset, max_distance), bool(guide.get("cyclic")))
            )
        collection = _named_collection(collection_name)
        records = []
        for name, role, points, cyclic in prepared:
            obj = _curve_object(
                name,
                collection,
                points,
                cyclic,
                {
                    "blender_mcp_retopology_guide": True,
                    "blender_mcp_guide_role": role,
                    "blender_mcp_guide_source": source.name,
                },
            )
            records.append(
                {
                    "name": obj.name,
                    "role": role,
                    "cyclic": cyclic,
                    "point_count": len(points),
                    "world_points": [list(point) for point in points],
                }
            )
        return {
            "source": source.name,
            "collection": collection.name,
            "coordinate_space": "WORLD",
            "guides": records,
            "created_guide_objects": [record["name"] for record in records],
        }

    def create_surface_section(
        self,
        source_object_name,
        plane_origin,
        plane_normal,
        vertex_count,
        name=None,
        collection_name="Retopology Guides",
        component_index=0,
        cyclic=True,
        projection_offset=0.0,
    ):
        source = get_mesh_object(source_object_name)
        origin = _vector(plane_origin, "plane_origin")
        normal = _vector(plane_normal, "plane_normal", nonzero=True).normalized()
        count = int(vertex_count)
        selected_component = int(component_index)
        if count < 3 or count > 10000:
            raise ValueError("vertex_count must be between 3 and 10000")
        if selected_component < 0:
            raise ValueError("component_index must be non-negative")
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = source.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bmesh.ops.transform(bm, matrix=evaluated.matrix_world, verts=bm.verts)
            cut = bmesh.ops.bisect_plane(
                bm,
                geom=[*bm.verts, *bm.edges, *bm.faces],
                dist=1e-6,
                plane_co=origin,
                plane_no=normal,
                clear_inner=False,
                clear_outer=False,
            )
            components = _ordered_edge_components(
                [element for element in cut["geom_cut"] if isinstance(element, bmesh.types.BMEdge)]
            )
        finally:
            bm.free()
            evaluated.to_mesh_clear()
        if not components:
            raise ValueError("The plane does not produce a usable edge section on the evaluated source")
        components.sort(key=lambda item: _polyline_length(item[0], item[1]), reverse=True)
        if selected_component >= len(components):
            raise ValueError(f"component_index {selected_component} is out of range for {len(components)} sections")
        points, detected_cyclic = components[selected_component]
        if cyclic and not detected_cyclic:
            raise ValueError("Selected section is open; call with cyclic=False or choose another component")
        projected = _project_world_points(
            source, _resample_polyline(points, count, bool(cyclic)), _finite(projection_offset, "projection_offset")
        )
        collection = _named_collection(collection_name)
        obj = _curve_object(
            name or f"{source.name}_Section",
            collection,
            projected,
            bool(cyclic),
            {
                "blender_mcp_retopology_guide": True,
                "blender_mcp_guide_role": "SECTION",
                "blender_mcp_guide_source": source.name,
            },
        )
        return {
            "source": source.name,
            "guide_object": obj.name,
            "collection": collection.name,
            "coordinate_space": "WORLD",
            "plane_origin": list(origin),
            "plane_normal": list(normal),
            "selected_component": selected_component,
            "detected_cyclic": detected_cyclic,
            "vertex_count": len(projected),
            "components": [
                {"index": index, "cyclic": item[1], "point_count": len(item[0]), "length": _polyline_length(*item)}
                for index, item in enumerate(components)
            ],
        }

    def set_retopology_features(
        self,
        object_name,
        edge_indices=None,
        detect_source_object_name=None,
        source_dihedral_angle=None,
        include_material_boundaries=False,
        guide_object_names=None,
        guide_distance=0.01,
        apply_detected=False,
        seam=None,
        sharp=None,
        crease=None,
        bevel_weight=None,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        explicit = set(_ensure_indices(obj.data.edges, edge_indices, "edge_indices"))
        if all(value is None for value in (seam, sharp, crease, bevel_weight)):
            raise ValueError("At least one of seam, sharp, crease, or bevel_weight must be supplied")
        if crease is not None and not 0.0 <= _finite(crease, "crease") <= 1.0:
            raise ValueError("crease must be between 0 and 1")
        if bevel_weight is not None and not 0.0 <= _finite(bevel_weight, "bevel_weight") <= 1.0:
            raise ValueError("bevel_weight must be between 0 and 1")
        distance = _positive(guide_distance, "guide_distance", allow_zero=True)
        candidates, candidate_reasons, source_features = set(), {}, []
        if source_dihedral_angle is not None or include_material_boundaries:
            if not detect_source_object_name:
                raise ValueError("detect_source_object_name is required for source-derived candidates")
            source = get_mesh_object(detect_source_object_name)
            threshold = None
            if source_dihedral_angle is not None:
                threshold = _finite(source_dihedral_angle, "source_dihedral_angle")
                if not 0.0 <= threshold <= math.pi:
                    raise ValueError("source_dihedral_angle must be between 0 and pi radians")
            with _read_bmesh(source) as bm:
                for edge in bm.edges:
                    reasons = []
                    if threshold is not None and len(edge.link_faces) == 2 and edge.calc_face_angle(0.0) >= threshold:
                        reasons.append("SOURCE_DIHEDRAL")
                    if (
                        include_material_boundaries
                        and len(edge.link_faces) == 2
                        and (edge.link_faces[0].material_index != edge.link_faces[1].material_index)
                    ):
                        reasons.append("SOURCE_MATERIAL_BOUNDARY")
                    if reasons:
                        point = source.matrix_world @ ((edge.verts[0].co + edge.verts[1].co) * 0.5)
                        source_features.append((point, reasons))
        guide_points = []
        for guide_name in guide_object_names or []:
            guide = bpy.data.objects.get(guide_name)
            if guide is None:
                raise ValueError(f"Guide object not found: {guide_name}")
            guide_points.extend((point, guide.name) for point in _curve_world_points(guide))
        for edge in obj.data.edges:
            point = obj.matrix_world @ (
                (obj.data.vertices[edge.vertices[0]].co + obj.data.vertices[edge.vertices[1]].co) * 0.5
            )
            reasons = [
                reason
                for source_point, source_reasons in source_features
                if (point - source_point).length <= distance
                for reason in source_reasons
            ]
            reasons.extend(
                f"GUIDE:{name}" for guide_point, name in guide_points if (point - guide_point).length <= distance
            )
            if reasons:
                candidates.add(edge.index)
                candidate_reasons[edge.index] = sorted(set(reasons))
        changed = sorted(explicit | (candidates if apply_detected else set()))
        if not changed:
            raise ValueError("No explicit edges were supplied and no detected candidates were activated")
        if seam is not None:
            for index in changed:
                obj.data.edges[index].use_seam = bool(seam)
        for attribute_name, data_type, value in (
            ("sharp_edge", "BOOLEAN", sharp),
            ("crease_edge", "FLOAT", crease),
            ("bevel_weight_edge", "FLOAT", bevel_weight),
        ):
            if value is not None:
                attribute = _mesh_attribute(obj.data, attribute_name, data_type)
                for index in changed:
                    attribute.data[index].value = value
        obj.data.update()
        return {
            "name": obj.name,
            "changed_edge_indices": changed,
            "explicit_edge_indices": sorted(explicit),
            "detected_candidate_edge_indices": sorted(candidates),
            "candidate_reasons": {str(index): candidate_reasons[index] for index in sorted(candidate_reasons)},
            "applied_detected": bool(apply_detected),
            "marks": {"seam": seam, "sharp": sharp, "crease": crease, "bevel_weight": bevel_weight},
            "topology_revision": topology_revision(obj),
        }

    def add_support_loops(
        self,
        object_name,
        edge_indices,
        width,
        side="BOTH",
        clamp=True,
        corner_policy="MITER",
        source_object_name=None,
        projection_offset=0.0,
        subdivision_levels=2,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        indices = _ensure_indices(obj.data.edges, edge_indices, "edge_indices", required=True)
        factor = _positive(width, "width")
        if factor > 10.0:
            raise ValueError("width is Blender's Edge Slide factor and must not exceed 10")
        side, corner_policy = str(side).upper(), str(corner_policy).upper()
        if side not in {"BOTH", "LEFT", "RIGHT"}:
            raise ValueError("side must be BOTH, LEFT, or RIGHT")
        if corner_policy not in {"MITER", "CAP_ENDPOINTS"}:
            raise ValueError("corner_policy must be MITER or CAP_ENDPOINTS")
        levels = int(subdivision_levels)
        if not 0 <= levels <= 6:
            raise ValueError("subdivision_levels must be between 0 and 6")
        with _read_bmesh(obj) as bm:
            selected = [bm.edges[index] for index in indices]
            if any(not edge.is_manifold for edge in selected):
                raise ValueError("Support-loop input edges must all be manifold")
            degree = {}
            for edge in selected:
                for vertex in edge.verts:
                    degree[vertex] = degree.get(vertex, 0) + 1
            if any(value > 2 for value in degree.values()):
                raise ValueError("Support-loop input must contain non-branching edge chains")
        before = mesh_counts(obj)
        with edit_mesh(obj, edge_indices=indices):
            result = bpy.ops.mesh.offset_edge_loops_slide(
                MESH_OT_offset_edge_loops={"use_cap_endpoint": corner_policy == "CAP_ENDPOINTS"},
                TRANSFORM_OT_edge_slide={
                    "value": -factor if side == "LEFT" else factor,
                    "single_side": side != "BOTH",
                    "use_even": True,
                    "flipped": side == "LEFT",
                    "use_clamp": bool(clamp),
                    "correct_uv": True,
                },
            )
            _require_finished(result, "Offset Edge Loops")
        after = mesh_counts(obj)
        created_vertices = list(range(before["vertices"], after["vertices"]))
        created_edges = list(range(before["edges"], after["edges"]))
        created_faces = list(range(before["polygons"], after["polygons"]))
        source = get_mesh_object(source_object_name) if source_object_name else None
        projection_failed = []
        if source and created_vertices:
            with _editable_bmesh(obj) as bm:
                projection_failed = _project_vertices(
                    obj, [bm.verts[index] for index in created_vertices], source, projection_offset
                )
        with _read_bmesh(obj) as bm:
            invalid = [edge.index for edge in bm.edges if len(edge.link_faces) > 2]
        if invalid:
            raise ValueError(f"Support loops would leave non-manifold edges: {invalid[:20]}")
        subdivision = next((modifier for modifier in obj.modifiers if modifier.type == "SUBSURF"), None)
        if levels:
            subdivision = subdivision or obj.modifiers.new(name="RetopologySubdivision", type="SUBSURF")
            subdivision.levels = levels
            subdivision.render_levels = levels
        return {
            "name": obj.name,
            "counts_before": before,
            "counts_after": after,
            "created_vertex_indices": created_vertices,
            "created_edge_indices": created_edges,
            "created_face_indices": created_faces,
            "failed_projection_vertex_indices": projection_failed,
            "manifold": not invalid,
            "subdivision_modifier": subdivision.name if subdivision else None,
            "modifier_order": _modifier_order(obj),
            "topology_revision": topology_revision(obj),
        }

    def transfer_mesh_attributes(
        self,
        source_object_name,
        object_name,
        data_types,
        modifier_name="RetopologyDataTransfer",
        vertex_mapping="POLYINTERP_NEAREST",
        edge_mapping="NEAREST",
        loop_mapping="POLYINTERP_NEAREST",
        polygon_mapping="NEAREST",
        use_object_transform=True,
        max_distance=None,
        source_layers="ALL",
        destination_layers="NAME",
        mix_mode="REPLACE",
        mix_factor=1.0,
        apply=False,
    ):
        source, obj = get_mesh_object(source_object_name), get_mesh_object(object_name)
        if source == obj:
            raise ValueError("source_object_name must differ from object_name")
        requested = {str(value).upper() for value in data_types}
        unknown = requested - _TRANSFER_TYPES
        if not requested or unknown:
            raise ValueError(
                f"data_types must be a non-empty subset of {sorted(_TRANSFER_TYPES)}; unknown={sorted(unknown)}"
            )
        if "UVS" in requested and not source.data.uv_layers:
            raise ValueError(f"Source '{source.name}' has no UV layers to transfer")
        if "VERTEX_GROUPS" in requested and not source.vertex_groups:
            raise ValueError(f"Source '{source.name}' has no vertex groups to transfer")
        if "COLOR_ATTRIBUTES" in requested and not source.data.color_attributes:
            raise ValueError(f"Source '{source.name}' has no color attributes to transfer")
        if "MATERIAL_INDICES" in requested and not source.material_slots:
            raise ValueError(f"Source '{source.name}' has no material slots to map")
        factor = _finite(mix_factor, "mix_factor")
        if not 0.0 <= factor <= 1.0:
            raise ValueError("mix_factor must be between 0 and 1")
        maximum = _positive(max_distance, "max_distance", allow_zero=True) if max_distance is not None else 1.0
        source_layers, destination_layers = str(source_layers).upper(), str(destination_layers).upper()
        if source_layers not in {"ACTIVE", "ALL"} or destination_layers not in {"NAME", "INDEX"}:
            raise ValueError("source_layers must be ACTIVE/ALL and destination_layers must be NAME/INDEX")
        modifier_types = requested - {"MATERIAL_INDICES"}
        modifier = None
        if modifier_types:
            modifier = obj.modifiers.get(modifier_name)
            if modifier is not None and modifier.type != "DATA_TRANSFER":
                raise ValueError(f"Modifier '{modifier_name}' exists but is type {modifier.type}, not DATA_TRANSFER")
            modifier = modifier or obj.modifiers.new(name=modifier_name, type="DATA_TRANSFER")
            modifier.object = source
            modifier.use_object_transform = bool(use_object_transform)
            modifier.use_max_distance = max_distance is not None
            modifier.max_distance = maximum
            modifier.mix_mode = str(mix_mode).upper()
            modifier.mix_factor = factor
            # Assignment through RNA deliberately validates these public Blender enums.
            modifier.vert_mapping = str(vertex_mapping).upper()
            modifier.edge_mapping = str(edge_mapping).upper()
            modifier.loop_mapping = str(loop_mapping).upper()
            modifier.poly_mapping = str(polygon_mapping).upper()
            modifier.data_types_verts = {
                item
                for key, item in {
                    "VERTEX_GROUPS": "VGROUP_WEIGHTS",
                    "COLOR_ATTRIBUTES": "COLOR_VERTEX",
                    "BEVEL_WEIGHTS": "BEVEL_WEIGHT_VERT",
                }.items()
                if key in modifier_types
            }
            modifier.data_types_edges = {
                item
                for key, item in {
                    "SEAMS": "SEAM",
                    "CREASES": "CREASE",
                    "BEVEL_WEIGHTS": "BEVEL_WEIGHT_EDGE",
                    "SHARP_EDGES": "SHARP_EDGE",
                }.items()
                if key in modifier_types
            }
            modifier.data_types_loops = {
                item
                for key, item in {
                    "UVS": "UV",
                    "COLOR_ATTRIBUTES": "COLOR_CORNER",
                    "CUSTOM_NORMALS": "CUSTOM_NORMAL",
                }.items()
                if key in modifier_types
            }
            modifier.data_types_polys = {"SMOOTH"} if "SMOOTH_SHADING" in modifier_types else set()
            modifier.layers_vgroup_select_src = source_layers
            modifier.layers_vgroup_select_dst = destination_layers
            modifier.layers_uv_select_src = source_layers
            modifier.layers_uv_select_dst = destination_layers
            if apply:
                apply_modifier(obj, modifier)
        material_faces = []
        if "MATERIAL_INDICES" in requested:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = source.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
                polygons = [tuple(polygon.vertices) for polygon in mesh.polygons if len(polygon.vertices) >= 3]
                tree = _bvh_class().FromPolygons(vertices, polygons, all_triangles=False, epsilon=0.0)
                destination_indices = {}
                for index, slot in enumerate(source.material_slots):
                    material = slot.material
                    if material is None:
                        continue
                    destination = next(
                        (
                            slot_index
                            for slot_index, target_slot in enumerate(obj.material_slots)
                            if target_slot.material == material
                        ),
                        None,
                    )
                    if destination is None:
                        obj.data.materials.append(material)
                        destination = len(obj.material_slots) - 1
                    destination_indices[index] = destination
                for polygon in obj.data.polygons:
                    _hit, _normal, source_face, _distance = tree.find_nearest(obj.matrix_world @ polygon.center)
                    if source_face is not None:
                        polygon.material_index = destination_indices.get(mesh.polygons[source_face].material_index, 0)
                        material_faces.append(polygon.index)
            finally:
                evaluated.to_mesh_clear()
        return {
            "name": obj.name,
            "source": source.name,
            "data_types": sorted(requested),
            "modifier": None if modifier is None or apply else modifier.name,
            "applied": bool(apply and modifier_types),
            "material_index_face_indices": material_faces,
            "modifier_order": _modifier_order(obj),
            "topology_revision": topology_revision(obj),
        }

    def unwrap_retopology_uvs(
        self,
        object_name,
        uv_map_name="RetopologyUV",
        method="ANGLE_BASED",
        replace_existing=False,
        average_island_scale=True,
        minimize_stretch_iterations=10,
        pack_islands=True,
        margin=0.001,
    ):
        obj = get_mesh_object(object_name)
        name, method = _require_name(uv_map_name, "uv_map_name"), str(method).upper()
        if method not in {"ANGLE_BASED", "CONFORMAL", "MINIMUM_STRETCH"}:
            raise ValueError("method must be ANGLE_BASED, CONFORMAL, or MINIMUM_STRETCH")
        iterations = int(minimize_stretch_iterations)
        if not 0 <= iterations <= 10000:
            raise ValueError("minimize_stretch_iterations must be between 0 and 10000")
        margin = _positive(margin, "margin", allow_zero=True)
        if margin > 1.0:
            raise ValueError("margin must not exceed 1")
        existing = obj.data.uv_layers.get(name)
        if existing and not replace_existing:
            raise ValueError(f"UV map '{name}' already exists; set replace_existing=True to replace only that map")
        if existing:
            obj.data.uv_layers.remove(existing)
        layer = obj.data.uv_layers.new(name=name, do_init=False)
        obj.data.uv_layers.active = layer
        with edit_mesh(obj):
            _require_finished(bpy.ops.uv.unwrap(method=method, margin=margin), "UV Unwrap")
            if average_island_scale:
                _require_finished(
                    bpy.ops.uv.average_islands_scale(scale_uv=False, shear=False), "Average Islands Scale"
                )
            if iterations:
                _require_finished(
                    bpy.ops.uv.minimize_stretch(fill_holes=True, blend=0.0, iterations=iterations), "Minimize Stretch"
                )
            if pack_islands:
                _require_finished(
                    bpy.ops.uv.pack_islands(rotate=True, scale=True, margin_method="ADD", margin=margin), "Pack Islands"
                )
        obj.data.uv_layers.active = obj.data.uv_layers.get(name)
        uv_layer = obj.data.uv_layers[name]
        zero_area, outside, stretch, density = [], set(), [], []
        for polygon in obj.data.polygons:
            uvs = [uv_layer.data[index].uv for index in polygon.loop_indices]
            if len(uvs) < 3:
                continue
            uv_area = (
                abs(
                    sum(
                        uvs[index].x * uvs[(index + 1) % len(uvs)].y - uvs[(index + 1) % len(uvs)].x * uvs[index].y
                        for index in range(len(uvs))
                    )
                )
                * 0.5
            )
            if uv_area <= 1e-12:
                zero_area.append(polygon.index)
            if any(component < -1e-8 or component > 1.0 + 1e-8 for uv in uvs for component in uv):
                outside.add(polygon.index)
            if polygon.area > 1e-12 and uv_area > 1e-12:
                density.append(math.sqrt(uv_area / polygon.area))
                stretch.append(max(polygon.area / uv_area, uv_area / polygon.area))
        overlaps = RetopologyHandlersMixin._uv_overlap_pairs(obj.data, 100)
        seam_edges = {edge.index for edge in obj.data.edges if edge.use_seam}
        adjacency = {polygon.index: set() for polygon in obj.data.polygons}
        edge_faces = {}
        for polygon in obj.data.polygons:
            for key in polygon.edge_keys:
                edge_faces.setdefault(key, []).append(polygon.index)
        for edge in obj.data.edges:
            if edge.index not in seam_edges:
                for first in edge_faces.get(edge.key, []):
                    adjacency[first].update(second for second in edge_faces[edge.key] if second != first)
        islands, remaining = 0, set(adjacency)
        while remaining:
            islands += 1
            queue = [remaining.pop()]
            while queue:
                connected = adjacency[queue.pop()] & remaining
                remaining -= connected
                queue.extend(connected)
        mean_density = statistics.fmean(density) if density else None
        variation = (
            statistics.pstdev(density) / mean_density
            if mean_density and len(density) > 1
            else (0.0 if density else None)
        )
        return {
            "name": obj.name,
            "uv_map": name,
            "method": method,
            "island_count": islands,
            "zero_area_face_indices": zero_area,
            "overlap_face_pairs": overlaps,
            "overlap_check_skipped": overlaps is None,
            "out_of_range_face_indices": sorted(outside),
            "stretch": {
                "mean_area_ratio": statistics.fmean(stretch) if stretch else None,
                "maximum_area_ratio": max(stretch, default=None),
            },
            "texel_density": {"mean": mean_density, "coefficient_of_variation": variation},
            "topology_revision": topology_revision(obj),
        }

    def create_bake_cage(
        self,
        object_name,
        high_poly_object_names,
        name=None,
        collection_name="Retopology Bake Cages",
        offset=0.02,
        vertex_group=None,
        validate_enclosure=True,
    ):
        obj = get_mesh_object(object_name)
        high_objects = [get_mesh_object(value) for value in high_poly_object_names]
        if not high_objects:
            raise ValueError("high_poly_object_names must contain at least one mesh")
        if obj in high_objects:
            raise ValueError("The low-poly object cannot also be a high-poly source")
        amount = _positive(offset, "offset", allow_zero=True)
        group = obj.vertex_groups.get(vertex_group) if vertex_group else None
        if vertex_group and group is None:
            raise ValueError(f"Vertex group not found on '{obj.name}': {vertex_group}")
        mesh = obj.data.copy()
        cage = bpy.data.objects.new(name or f"{obj.name}_Cage", mesh)
        collection = _named_collection(collection_name)
        collection.objects.link(cage)
        cage.matrix_world = obj.matrix_world.copy()
        cage.hide_render = True
        cage.display_type = "WIRE"
        cage.show_in_front = True
        cage["blender_mcp_bake_cage_for"] = obj.name
        for vertex in mesh.vertices:
            weight = 1.0
            if group is not None:
                try:
                    weight = group.weight(vertex.index)
                except RuntimeError:
                    weight = 0.0
            vertex.co += vertex.normal.normalized() * amount * weight
        mesh.update()
        topology_identical = (
            len(mesh.vertices) == len(obj.data.vertices)
            and [tuple(edge.vertices) for edge in mesh.edges] == [tuple(edge.vertices) for edge in obj.data.edges]
            and [tuple(poly.vertices) for poly in mesh.polygons] == [tuple(poly.vertices) for poly in obj.data.polygons]
        )
        outside, ray_misses, self_intersections = [], [], []
        if validate_enclosure:
            cage_tree = _world_bvh(cage)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            for high in high_objects:
                evaluated = high.evaluated_get(depsgraph)
                high_mesh = evaluated.to_mesh()
                try:
                    for vertex in high_mesh.vertices:
                        world = evaluated.matrix_world @ vertex.co
                        hit = _nearest_projection(cage_tree, world)
                        if hit and (world - hit[0]).dot(hit[1]) > 1e-6:
                            outside.append({"object": high.name, "vertex_index": vertex.index})
                finally:
                    evaluated.to_mesh_clear()
            high_trees = [_world_bvh(high) for high in high_objects]
            normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
            for vertex in obj.data.vertices:
                origin = obj.matrix_world @ vertex.co
                direction = (normal_matrix @ vertex.normal).normalized()
                found = any(
                    tree.ray_cast(origin, direction, 1.84467e19)[0] is not None
                    or tree.ray_cast(origin, -direction, 1.84467e19)[0] is not None
                    for tree in high_trees
                )
                if not found:
                    ray_misses.append(vertex.index)
            with _read_bmesh(cage) as bm:
                self_intersections = RetopologyHandlersMixin._self_intersections(bm, 100)
        return {
            "source_low_poly": obj.name,
            "high_poly_objects": [item.name for item in high_objects],
            "cage_object": cage.name,
            "collection": collection.name,
            "offset": amount,
            "vertex_group": vertex_group,
            "topology_identical": topology_identical,
            "counts": mesh_counts(cage),
            "validation": {
                "high_poly_samples_outside": outside[:100],
                "outside_count": len(outside),
                "normal_ray_miss_vertex_indices": ray_misses[:100],
                "ray_miss_count": len(ray_misses),
                "self_intersection_face_pairs": self_intersections,
            },
        }

    def bake_retopology_maps(
        self,
        object_name,
        high_poly_object_names,
        map_type,
        output_path,
        width=2048,
        height=2048,
        uv_map_name=None,
        cage_object_name=None,
        cage_extrusion=0.0,
        max_ray_distance=0.0,
        margin=16,
        normal_space="TANGENT",
        normal_swizzle=("POS_X", "POS_Y", "POS_Z"),
        overwrite=False,
        confirm=False,
    ):
        if not confirm:
            raise ValueError("Baking is expensive and writes a file; call again with confirm=True")
        obj = get_mesh_object(object_name)
        high_objects = [get_mesh_object(value) for value in high_poly_object_names]
        if not high_objects or obj in high_objects:
            raise ValueError("Provide at least one distinct high-poly mesh object")
        bake_type = str(map_type).upper()
        bake_types = {
            "NORMAL": "NORMAL",
            "DISPLACEMENT": "DISPLACEMENT",
            "AO": "AO",
            "POSITION": "POSITION",
            "DIFFUSE": "DIFFUSE",
            "ROUGHNESS": "ROUGHNESS",
            "EMISSION": "EMIT",
        }
        if bake_type not in bake_types:
            raise ValueError(f"map_type must be one of {sorted(bake_types)}")
        path = Path(output_path).expanduser()
        if not path.is_absolute():
            raise ValueError("output_path must be absolute")
        if not path.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {path.parent}")
        existed = path.exists()
        if existed and not overwrite:
            raise ValueError(f"Output file already exists: {path}; set overwrite=True to replace it")
        width, height, margin = int(width), int(height), int(margin)
        if not 1 <= width <= 32768 or not 1 <= height <= 32768:
            raise ValueError("width and height must be between 1 and 32768")
        if not 0 <= margin <= 32767:
            raise ValueError("margin must be between 0 and 32767 pixels")
        if not obj.data.uv_layers:
            raise ValueError("The low-poly mesh has no UV map; run unwrap_retopology_uvs first")
        uv_layer = obj.data.uv_layers.get(uv_map_name) if uv_map_name else obj.data.uv_layers.active
        if uv_layer is None or not uv_layer.data:
            raise ValueError(f"UV map is missing or empty: {uv_map_name or '<active>'}")
        cage = get_mesh_object(cage_object_name) if cage_object_name else None
        if cage and (
            len(cage.data.vertices) != len(obj.data.vertices)
            or [tuple(edge.vertices) for edge in cage.data.edges] != [tuple(edge.vertices) for edge in obj.data.edges]
            or [tuple(face.vertices) for face in cage.data.polygons]
            != [tuple(face.vertices) for face in obj.data.polygons]
        ):
            raise ValueError("The bake cage topology does not match the low-poly mesh")
        extrusion = _positive(cage_extrusion, "cage_extrusion", allow_zero=True)
        ray_distance = _positive(max_ray_distance, "max_ray_distance", allow_zero=True)
        normal_space = str(normal_space).upper()
        if normal_space not in {"TANGENT", "OBJECT"}:
            raise ValueError("normal_space must be TANGENT or OBJECT")
        valid_swizzle = {f"{sign}_{axis}" for sign in ("POS", "NEG") for axis in "XYZ"}
        if len(normal_swizzle) != 3 or any(str(value).upper() not in valid_swizzle for value in normal_swizzle):
            raise ValueError(f"normal_swizzle must contain three values from {sorted(valid_swizzle)}")
        extension_formats = {
            ".png": "PNG",
            ".tif": "TIFF",
            ".tiff": "TIFF",
            ".exr": "OPEN_EXR",
            ".jpg": "JPEG",
        }
        if path.suffix.lower() not in extension_formats:
            raise ValueError("output_path extension must be .png, .tif, .tiff, .exr, or .jpg")
        image = bpy.data.images.new(name=f"{obj.name}_{bake_type}", width=width, height=height, alpha=False)
        image.filepath_raw = str(path)
        image.file_format = extension_formats[path.suffix.lower()]
        scene = bpy.context.scene
        prior_engine, prior_uv = scene.render.engine, obj.data.uv_layers.active
        material_states, temporary_material = [], None
        try:
            obj.data.uv_layers.active = uv_layer
            if not obj.material_slots:
                temporary_material = bpy.data.materials.new(name=f"{obj.name}_BakeTarget")
                temporary_material.use_nodes = True
                obj.data.materials.append(temporary_material)
            for slot in obj.material_slots:
                material = slot.material
                if material is None:
                    continue
                previous_use_nodes = material.use_nodes
                material.use_nodes = True
                nodes = material.node_tree.nodes
                previous_active = nodes.active
                node = nodes.new("ShaderNodeTexImage")
                node.image = image
                nodes.active = node
                material_states.append((material, previous_use_nodes, previous_active, node))
            if not material_states:
                raise ValueError("The low-poly object has no usable material slot for an active bake image")
            with preserve_mode_and_selection():
                scene.render.engine = "CYCLES"
                bpy.ops.object.select_all(action="DESELECT")
                for high in high_objects:
                    high.select_set(True)
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                settings = scene.render.bake
                settings.use_selected_to_active = True
                settings.use_cage = cage is not None
                settings.cage_object = cage
                settings.cage_extrusion = extrusion
                settings.max_ray_distance = ray_distance
                settings.margin = margin
                settings.normal_space = normal_space
                settings.normal_r, settings.normal_g, settings.normal_b = tuple(
                    str(value).upper() for value in normal_swizzle
                )
                _require_finished(bpy.ops.object.bake(type=bake_types[bake_type]), f"{bake_type} Bake")
            image.save_render(filepath=str(path), scene=scene)
            if not path.is_file():
                raise RuntimeError(f"Bake completed but output file was not written: {path}")
        except Exception:
            if not existed:
                with contextlib.suppress(OSError):
                    os.unlink(path)
            raise
        finally:
            scene.render.engine = prior_engine
            obj.data.uv_layers.active = prior_uv
            for material, previous_use_nodes, previous_active, node in material_states:
                with contextlib.suppress(Exception):
                    material.node_tree.nodes.remove(node)
                with contextlib.suppress(Exception):
                    material.node_tree.nodes.active = previous_active
                material.use_nodes = previous_use_nodes
            if temporary_material:
                with contextlib.suppress(Exception):
                    obj.data.materials.pop(index=len(obj.data.materials) - 1)
                with contextlib.suppress(Exception):
                    bpy.data.materials.remove(temporary_material)
        return {
            "name": obj.name,
            "high_poly_objects": [item.name for item in high_objects],
            "map_type": bake_type,
            "image": image.name,
            "dimensions": [width, height],
            "uv_map": uv_layer.name,
            "cage_object": cage.name if cage else None,
            "output_path": str(path),
            "overwrote_existing": existed,
        }

    def test_deformation(
        self,
        object_name,
        frames,
        reference_frame=None,
        source_object_name=None,
        joint_vertex_groups=None,
        stretch_warning_ratio=1.25,
        area_warning_ratio=1.5,
        check_self_intersections=True,
        issue_limit=100,
    ):
        obj = get_mesh_object(object_name)
        source = get_mesh_object(source_object_name) if source_object_name else None
        if source == obj:
            raise ValueError("source_object_name must differ from object_name")
        if (
            not isinstance(frames, list)
            or not frames
            or any(isinstance(frame, bool) or not isinstance(frame, int) for frame in frames)
        ):
            raise ValueError("frames must be a non-empty list of integers")
        stretch_limit = _positive(stretch_warning_ratio, "stretch_warning_ratio")
        area_limit = _positive(area_warning_ratio, "area_warning_ratio")
        if stretch_limit < 1.0 or area_limit < 1.0:
            raise ValueError("stretch_warning_ratio and area_warning_ratio must be at least 1")
        issue_limit = max(1, min(int(issue_limit), 1000))
        group_indices = set()
        for name in joint_vertex_groups or []:
            group = obj.vertex_groups.get(name)
            if group is None:
                raise ValueError(f"Joint vertex group not found on '{obj.name}': {name}")
            for vertex in obj.data.vertices:
                with contextlib.suppress(RuntimeError):
                    if group.weight(vertex.index) > 0.0:
                        group_indices.add(vertex.index)
        scene = bpy.context.scene
        original_frame = scene.frame_current
        reference = original_frame if reference_frame is None else int(reference_frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        results = []
        try:
            scene.frame_set(reference)
            depsgraph.update()
            base_vertices, base_edges, base_polygons = _evaluated_mesh_snapshot(obj, depsgraph)
            base_lengths, base_areas, base_normals, base_volume = _surface_statistics(
                base_vertices, base_edges, base_polygons
            )
            for frame in frames:
                scene.frame_set(frame)
                depsgraph.update()
                vertices, edges, polygons = _evaluated_mesh_snapshot(obj, depsgraph)
                if edges != base_edges or polygons != base_polygons:
                    raise ValueError(
                        "The evaluated modifier stack changes topology across frames; correspondence is undefined"
                    )
                lengths, areas, normals, volume = _surface_statistics(vertices, edges, polygons)
                edge_ratios = [
                    current / original if original > 1e-12 else 1.0
                    for current, original in zip(lengths, base_lengths, strict=True)
                ]
                area_ratios = [
                    current / original if original > 1e-12 else 1.0
                    for current, original in zip(areas, base_areas, strict=True)
                ]
                stretched = [
                    index
                    for index, ratio in enumerate(edge_ratios)
                    if (ratio > stretch_limit or ratio < 1.0 / stretch_limit)
                    and (not group_indices or set(edges[index]) & group_indices)
                ]
                changed_area = [
                    index
                    for index, ratio in enumerate(area_ratios)
                    if (ratio > area_limit or ratio < 1.0 / area_limit)
                    and (not group_indices or set(polygons[index]) & group_indices)
                ]
                flipped = [
                    index
                    for index, (current, original) in enumerate(zip(normals, base_normals, strict=True))
                    if current.length_squared and original.length_squared and current.dot(original) < 0.0
                ]
                intersections = []
                if check_self_intersections and polygons:
                    tree = _bvh_class().FromPolygons(vertices, polygons, all_triangles=False, epsilon=0.0)
                    for first, second in tree.overlap(tree):
                        if first >= second or set(polygons[first]) & set(polygons[second]):
                            continue
                        intersections.append([first, second])
                        if len(intersections) >= issue_limit:
                            break
                conformity = None
                if source:
                    tree = _world_bvh(source)
                    conformity_indices = sorted(group_indices) if group_indices else range(len(vertices))
                    distances = []
                    misses = []
                    for index in conformity_indices:
                        hit = _nearest_projection(tree, vertices[index])
                        if hit is None:
                            misses.append(index)
                        else:
                            distances.append((index, hit[3]))
                    conformity = {
                        "coordinate_space": "WORLD",
                        "sample_count": len(distances) + len(misses),
                        "missed_vertex_indices": misses[:issue_limit],
                        "mean_distance": statistics.fmean(value for _index, value in distances) if distances else None,
                        "maximum_distance": max((value for _index, value in distances), default=None),
                        "worst_vertices": [
                            {"vertex_index": index, "distance": value}
                            for index, value in sorted(distances, key=lambda item: item[1], reverse=True)[:issue_limit]
                        ],
                    }
                results.append(
                    {
                        "frame": frame,
                        "edge_stretch": {
                            "minimum_ratio": min(edge_ratios, default=None),
                            "mean_ratio": statistics.fmean(edge_ratios) if edge_ratios else None,
                            "maximum_ratio": max(edge_ratios, default=None),
                            "warning_edge_indices": stretched[:issue_limit],
                            "warning_count": len(stretched),
                        },
                        "face_area_change": {
                            "minimum_ratio": min(area_ratios, default=None),
                            "mean_ratio": statistics.fmean(area_ratios) if area_ratios else None,
                            "maximum_ratio": max(area_ratios, default=None),
                            "warning_face_indices": changed_area[:issue_limit],
                            "warning_count": len(changed_area),
                        },
                        "signed_volume": volume,
                        "volume_ratio": volume / base_volume if abs(base_volume) > 1e-12 else None,
                        "flipped_face_indices": flipped[:issue_limit],
                        "flipped_face_count": len(flipped),
                        "self_intersection_face_pairs": intersections,
                        "source_conformity": conformity,
                    }
                )
        finally:
            scene.frame_set(original_frame)
        return {
            "name": obj.name,
            "source": source.name if source else None,
            "reference_frame": reference,
            "tested_frames": frames,
            "joint_vertex_groups": joint_vertex_groups or [],
            "thresholds": {"edge_stretch_ratio": stretch_limit, "face_area_ratio": area_limit},
            "results": results,
            "topology_revision": topology_revision(obj),
        }
