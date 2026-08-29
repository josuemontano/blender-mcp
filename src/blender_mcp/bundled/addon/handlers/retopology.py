# pyright: reportArgumentType=false, reportGeneralTypeIssues=false, reportOptionalSubscript=false
"""Blender-main-thread implementations for production retopology tools."""

import contextlib
import hashlib
import itertools
import math
import statistics

from collections import Counter

import bmesh
import bpy
import mathutils

from ..helpers import (
    apply_modifier,
    edit_mesh,
    get_mesh_object,
    mesh_counts,
    paginate,
    preserve_mode_and_selection,
    sync_from_editmode,
)

_RETARGET_SOURCES = "blender_mcp_retopology_sources"
_CHECKPOINT_COLLECTION = "_BlenderMCP_Retopology_Checkpoints"
_CHECKPOINT_TARGET = "blender_mcp_checkpoint_target"
_CHECKPOINT_LABEL = "blender_mcp_checkpoint_name"
_MAX_INSPECT_LIMIT = 500


def _bvh_class():
    from mathutils.bvhtree import BVHTree

    return BVHTree


def _kd_tree_class():
    from mathutils.kdtree import KDTree

    return KDTree


def _finite(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _positive(value, label, *, allow_zero=False):
    value = _finite(value, label)
    if value < 0 if allow_zero else value <= 0:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be {qualifier}")
    return value


def _vector(value, label, *, nonzero=False):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must contain exactly three numbers")
    vector = mathutils.Vector(tuple(_finite(component, label) for component in value))
    if nonzero and vector.length <= 1e-12:
        raise ValueError(f"{label} must not be zero")
    return vector


def _mesh_objects(names, label="source_object_names"):
    if not names:
        raise ValueError(f"{label} must contain at least one mesh object name")
    objects = []
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name:
            raise ValueError(f"Every item in {label} must be a non-empty string")
        obj = get_mesh_object(name)
        if obj.name not in seen:
            objects.append(obj)
            seen.add(obj.name)
    return objects


def topology_revision(obj):
    """Return a deterministic hash over base-mesh connectivity and coordinates."""
    sync_from_editmode(obj)
    digest = hashlib.blake2b(digest_size=16)
    mesh = obj.data
    digest.update(f"{len(mesh.vertices)}:{len(mesh.edges)}:{len(mesh.polygons)}|".encode())
    for vertex in mesh.vertices:
        digest.update(("{:.9g},{:.9g},{:.9g};".format(*tuple(vertex.co))).encode())
    for edge in mesh.edges:
        digest.update(f"{edge.vertices[0]},{edge.vertices[1]};".encode())
    for polygon in mesh.polygons:
        digest.update((",".join(str(index) for index in polygon.vertices) + ";").encode())
    return digest.hexdigest()


def _require_revision(obj, expected_revision):
    current = topology_revision(obj)
    if expected_revision is not None and expected_revision != current:
        raise ValueError(
            f"Stale topology revision for '{obj.name}': expected {expected_revision}, current {current}. "
            "Call inspect_retopology again and use its indices/revision."
        )
    return current


def _ensure_indices(elements, indices, label, *, required=False):
    if indices is None:
        if required:
            raise ValueError(f"{label} is required")
        return []
    if not isinstance(indices, (list, tuple)):
        raise ValueError(f"{label} must be a list of integer indices")
    result = []
    seen = set()
    total = len(elements)
    for raw in indices:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{label} contains non-integer index {raw!r}")
        if raw < 0 or raw >= total:
            raise ValueError(f"Index {raw} out of range for {label} (0-{total - 1})")
        if raw not in seen:
            result.append(raw)
            seen.add(raw)
    if required and not result:
        raise ValueError(f"{label} must not be empty")
    return result


@contextlib.contextmanager
def _editable_bmesh(obj):
    """Edit a base mesh through an owned BMesh while preserving UI context."""
    sync_from_editmode(obj)
    with preserve_mode_and_selection():
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            yield bm
            bm.normal_update()
            bm.to_mesh(obj.data)
            obj.data.update()
        finally:
            bm.free()


@contextlib.contextmanager
def _read_bmesh(obj):
    sync_from_editmode(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        yield bm
    finally:
        bm.free()


def _world_bvh(source):
    """Build a BVH in world coordinates from the dependency-graph result."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        polygons = [tuple(polygon.vertices) for polygon in mesh.polygons if len(polygon.vertices) >= 3]
        if not polygons:
            raise ValueError(f"Evaluated source '{source.name}' has no faces to project onto")
        return _bvh_class().FromPolygons(vertices, polygons, all_triangles=False, epsilon=0.0)
    finally:
        evaluated.to_mesh_clear()


def _nearest_projection(tree, world_point, offset=0.0, max_distance=None):
    distance_limit = float(max_distance) if max_distance is not None else 1.84467e19
    hit, normal, face_index, distance = tree.find_nearest(world_point, distance_limit)
    if hit is None:
        return None
    return hit + normal.normalized() * offset, normal.normalized(), face_index, float(distance)


def _ray_projection(
    tree,
    world_point,
    direction,
    max_distance,
    positive_direction,
    negative_direction,
    backface_policy,
    offset,
):
    limit = float(max_distance) if max_distance is not None else 1.84467e19
    candidates = []
    for sign, enabled in ((1.0, positive_direction), (-1.0, negative_direction)):
        if not enabled:
            continue
        ray = direction.normalized() * sign
        hit, normal, face_index, distance = tree.ray_cast(world_point, ray, limit)
        if hit is None:
            continue
        if backface_policy == "CULL" and normal.dot(-ray) <= 0.0:
            continue
        candidates.append((float(distance), hit + normal.normalized() * offset, normal.normalized(), face_index))
    if not candidates:
        return None
    distance, hit, normal, face_index = min(candidates, key=lambda item: item[0])
    return hit, normal, face_index, distance


def _project_vertices(obj, vertices, source, offset=0.0):
    tree = _world_bvh(source)
    inverse = obj.matrix_world.inverted_safe()
    failed = []
    for vertex in vertices:
        projected = _nearest_projection(tree, obj.matrix_world @ vertex.co, offset)
        if projected is None:
            failed.append(vertex.index)
            continue
        vertex.co = inverse @ projected[0]
    return failed


def _modifier_order(obj):
    return [
        {"index": index, "name": modifier.name, "type": modifier.type} for index, modifier in enumerate(obj.modifiers)
    ]


def _move_before_subdivision(obj, modifier):
    subdivisions = [index for index, candidate in enumerate(obj.modifiers) if candidate.type == "SUBSURF"]
    if subdivisions:
        obj.modifiers.move(obj.modifiers.find(modifier.name), min(subdivisions))


def _configure_shrinkwrap_modifier(
    obj,
    target,
    modifier_name,
    wrap_method,
    wrap_mode,
    offset,
    project_limit,
    project_axes,
    positive_direction,
    negative_direction,
    cull_face,
    invert_cull,
    auxiliary_target,
    vertex_group,
    invert_vertex_group,
):
    modifier = obj.modifiers.get(modifier_name)
    if modifier is not None and modifier.type != "SHRINKWRAP":
        raise ValueError(f"Modifier '{modifier_name}' exists but is type {modifier.type}, not SHRINKWRAP")
    if modifier is None:
        modifier = obj.modifiers.new(name=modifier_name, type="SHRINKWRAP")
    modifier.target = target
    modifier.wrap_method = wrap_method
    modifier.wrap_mode = wrap_mode
    modifier.offset = offset
    modifier.project_limit = project_limit
    modifier.use_project_x, modifier.use_project_y, modifier.use_project_z = project_axes
    modifier.use_positive_direction = positive_direction
    modifier.use_negative_direction = negative_direction
    modifier.cull_face = cull_face
    modifier.use_invert_cull = invert_cull
    modifier.auxiliary_target = auxiliary_target
    modifier.vertex_group = vertex_group
    modifier.invert_vertex_group = invert_vertex_group
    _move_before_subdivision(obj, modifier)
    return modifier


def _copy_evaluated_sources(mesh, sources, target_matrix):
    bm = bmesh.new()
    inverse = target_matrix.inverted_safe()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    try:
        for source in sources:
            evaluated = source.evaluated_get(depsgraph)
            evaluated_mesh = evaluated.to_mesh()
            try:
                transform = inverse @ evaluated.matrix_world
                vertex_map = {vertex.index: bm.verts.new(transform @ vertex.co) for vertex in evaluated_mesh.vertices}
                for polygon in evaluated_mesh.polygons:
                    with contextlib.suppress(ValueError):
                        bm.faces.new(tuple(vertex_map[index] for index in polygon.vertices))
            finally:
                evaluated.to_mesh_clear()
        bm.to_mesh(mesh)
    finally:
        bm.free()


def _new_target_geometry(mesh, geometry, size, grid_segments, sources, matrix_world):
    if geometry == "DUPLICATED_EVALUATED_SURFACE":
        _copy_evaluated_sources(mesh, sources, matrix_world)
        return
    bm = bmesh.new()
    try:
        if geometry == "SINGLE_VERTEX":
            bm.verts.new((0.0, 0.0, 0.0))
        elif geometry == "PLANE":
            half = size / 2.0
            vertices = [
                bm.verts.new(co) for co in ((-half, -half, 0), (half, -half, 0), (half, half, 0), (-half, half, 0))
            ]
            bm.faces.new(vertices)
        elif geometry == "GRID":
            u_count, v_count = grid_segments
            grid = []
            for v in range(v_count):
                y = size * (v / (v_count - 1) - 0.5)
                row = []
                for u in range(u_count):
                    x = size * (u / (u_count - 1) - 0.5)
                    row.append(bm.verts.new((x, y, 0.0)))
                grid.append(row)
            for v in range(v_count - 1):
                for u in range(u_count - 1):
                    bm.faces.new((grid[v][u], grid[v][u + 1], grid[v + 1][u + 1], grid[v + 1][u]))
        bm.to_mesh(mesh)
    finally:
        bm.free()


def _ordered_boundary_paths(bm):
    boundary_edges = {edge for edge in bm.edges if edge.is_boundary}
    paths = []
    while boundary_edges:
        seed = min(boundary_edges, key=lambda edge: edge.index)
        component_edges = set()
        queue = [seed]
        while queue:
            edge = queue.pop()
            if edge in component_edges:
                continue
            component_edges.add(edge)
            for vertex in edge.verts:
                queue.extend(
                    link for link in vertex.link_edges if link in boundary_edges and link not in component_edges
                )
        component_all = set(component_edges)
        degrees = Counter(vertex for edge in component_edges for vertex in edge.verts)
        if any(degree > 2 for degree in degrees.values()):
            paths.append({"closed": False, "branched": True, "vertex_indices": sorted(v.index for v in degrees)})
            boundary_edges -= component_all
            continue
        endpoints = [vertex for vertex, degree in degrees.items() if degree == 1]
        current = min(endpoints or list(degrees), key=lambda vertex: vertex.index)
        ordered = [current.index]
        previous = None
        while True:
            candidates = [edge for edge in current.link_edges if edge in component_edges and edge is not previous]
            if not candidates:
                break
            edge = min(candidates, key=lambda candidate: candidate.index)
            component_edges.remove(edge)
            next_vertex = edge.other_vert(current)
            if next_vertex.index == ordered[0]:
                break
            ordered.append(next_vertex.index)
            previous, current = edge, next_vertex
        paths.append({"closed": not endpoints, "branched": False, "vertex_indices": ordered})
        boundary_edges -= component_all
    return paths


def _connected_components(bm):
    remaining = set(bm.verts)
    components = []
    while remaining:
        seed = min(remaining, key=lambda vertex: vertex.index)
        found = set()
        queue = [seed]
        while queue:
            vertex = queue.pop()
            if vertex in found:
                continue
            found.add(vertex)
            queue.extend(edge.other_vert(vertex) for edge in vertex.link_edges if edge.other_vert(vertex) not in found)
        remaining -= found
        components.append(sorted(vertex.index for vertex in found))
    return components


def _percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _segment_distance(first_start, first_end, second_start, second_end):
    """Return the shortest distance between two finite 3D line segments."""
    first_direction = first_end - first_start
    second_direction = second_end - second_start
    offset = first_start - second_start
    first_length = first_direction.dot(first_direction)
    second_length = second_direction.dot(second_direction)
    cross = first_direction.dot(second_direction)
    first_offset = first_direction.dot(offset)
    second_offset = second_direction.dot(offset)
    denominator = first_length * second_length - cross * cross
    first_parameter = (
        0.0 if denominator <= 1e-20 else (cross * second_offset - first_offset * second_length) / denominator
    )
    first_parameter = max(0.0, min(1.0, first_parameter))
    second_parameter = 0.0 if second_length <= 1e-20 else (cross * first_parameter + second_offset) / second_length
    second_parameter = max(0.0, min(1.0, second_parameter))
    if first_length > 1e-20:
        first_parameter = max(0.0, min(1.0, (cross * second_parameter - first_offset) / first_length))
    return (first_start + first_direction * first_parameter - second_start - second_direction * second_parameter).length


def _symmetry_summary(bm):
    if not bm.verts:
        return {axis: {"matched": 0, "unmatched": 0, "tolerance": 0.0} for axis in "XYZ"}
    extent = max((vertex.co.length for vertex in bm.verts), default=1.0)
    tolerance = max(1e-6, extent * 1e-5)
    tree = _kd_tree_class()(len(bm.verts))
    for vertex in bm.verts:
        tree.insert(vertex.co, vertex.index)
    tree.balance()
    result = {}
    for axis_index, axis in enumerate("XYZ"):
        unmatched = 0
        for vertex in bm.verts:
            mirrored = vertex.co.copy()
            mirrored[axis_index] *= -1.0
            _co, _index, distance = tree.find(mirrored)
            unmatched += distance is None or distance > tolerance
        result[axis] = {"matched": len(bm.verts) - unmatched, "unmatched": unmatched, "tolerance": tolerance}
    return result


def _modifier_snapshot(modifier):
    result = {"name": modifier.name, "type": modifier.type, "show_viewport": modifier.show_viewport}
    for name in ("levels", "render_levels", "wrap_method", "wrap_mode", "offset", "project_limit", "merge_threshold"):
        if hasattr(modifier, name):
            value = getattr(modifier, name)
            result[name] = value.name if hasattr(value, "name") else value
    for name in ("target", "auxiliary_target", "mirror_object"):
        if hasattr(modifier, name):
            value = getattr(modifier, name)
            result[name] = value.name if value else None
    return result


class RetopologyHandlersMixin:
    """Provide inspection, projection, checkpoint, and topology handlers."""

    def create_retopology_target(
        self,
        source_object_names,
        name=None,
        initial_geometry="EMPTY",
        collection_name="Retopology",
        size=1.0,
        grid_segments=(4, 4),
        add_mirror=False,
        add_shrinkwrap=True,
        subdivision_levels=0,
    ):
        sources = _mesh_objects(source_object_names)
        geometry = str(initial_geometry).upper()
        allowed = {"EMPTY", "SINGLE_VERTEX", "PLANE", "GRID", "DUPLICATED_EVALUATED_SURFACE"}
        if geometry not in allowed:
            raise ValueError(f"initial_geometry must be one of {sorted(allowed)}")
        size = _positive(size, "size")
        if len(grid_segments) != 2 or any(isinstance(value, bool) or int(value) < 2 for value in grid_segments):
            raise ValueError("grid_segments must contain two integers, each at least 2")
        levels = int(subdivision_levels)
        if levels < 0 or levels > 6:
            raise ValueError("subdivision_levels must be between 0 and 6")
        if not isinstance(collection_name, str) or not collection_name.strip():
            raise ValueError("collection_name must be a non-empty string")

        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            collection = bpy.data.collections.new(collection_name)
            bpy.context.scene.collection.children.link(collection)
        mesh = bpy.data.meshes.new(f"{name or sources[0].name + '_Retopology'}_Mesh")
        obj = bpy.data.objects.new(name or f"{sources[0].name}_Retopology", mesh)
        collection.objects.link(obj)
        obj.matrix_world = sources[0].matrix_world.copy()
        obj[_RETARGET_SOURCES] = [source.name for source in sources]
        obj["blender_mcp_retopology_target"] = True
        _new_target_geometry(mesh, geometry, size, tuple(map(int, grid_segments)), sources, obj.matrix_world)

        if add_mirror:
            mirror = obj.modifiers.new(name="RetopologyMirror", type="MIRROR")
            mirror.use_axis = (True, False, False)
            mirror.use_mirror_merge = True
            mirror.use_clip = True
        if add_shrinkwrap:
            _configure_shrinkwrap_modifier(
                obj,
                sources[0],
                "RetopologyProjection",
                "NEAREST_SURFACEPOINT",
                "ON_SURFACE",
                0.0,
                0.0,
                (False, False, True),
                True,
                True,
                "OFF",
                False,
                None,
                "",
                False,
            )
        if levels:
            subdivision = obj.modifiers.new(name="RetopologySubdivision", type="SUBSURF")
            subdivision.levels = levels
            subdivision.render_levels = levels
        return {
            "name": obj.name,
            "collection": collection.name,
            "sources": [source.name for source in sources],
            "initial_geometry": geometry,
            **mesh_counts(obj),
            "modifier_order": _modifier_order(obj),
            "topology_revision": topology_revision(obj),
        }

    def inspect_retopology(self, object_name, selected_vertex_indices=None, adjacency_depth=1, limit=100, offset=0):
        obj = get_mesh_object(object_name)
        depth = int(adjacency_depth)
        if depth < 0 or depth > 8:
            raise ValueError("adjacency_depth must be between 0 and 8")
        selected = _ensure_indices(obj.data.vertices, selected_vertex_indices, "selected_vertex_indices")
        if len(selected) > 100:
            raise ValueError("selected_vertex_indices is limited to 100 vertices per inspection")
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated_object = obj.evaluated_get(depsgraph)
        evaluated_mesh = evaluated_object.to_mesh()
        try:
            evaluated_counts = {
                "vertices": len(evaluated_mesh.vertices),
                "edges": len(evaluated_mesh.edges),
                "polygons": len(evaluated_mesh.polygons),
            }
        finally:
            evaluated_object.to_mesh_clear()
        with _read_bmesh(obj) as bm:
            components = _connected_components(bm)
            boundary_paths = _ordered_boundary_paths(bm)
            non_manifold = [edge.index for edge in bm.edges if not edge.is_manifold]
            isolated_vertices = [vertex.index for vertex in bm.verts if not vertex.link_edges]
            isolated_edges = [edge.index for edge in bm.edges if not edge.link_faces]
            degenerates = [face.index for face in bm.faces if face.calc_area() <= 1e-12]
            face_types = Counter(
                "triangles" if len(face.verts) == 3 else "quads" if len(face.verts) == 4 else "ngons"
                for face in bm.faces
            )
            poles = {}
            for vertex in bm.verts:
                valence = len(vertex.link_edges)
                if valence != 4:
                    poles.setdefault(str(valence), []).append(vertex.index)
            edge_lengths = [edge.calc_length() for edge in bm.edges]
            aspects = []
            for face in bm.faces:
                lengths = [edge.calc_length() for edge in face.edges]
                if lengths and min(lengths) > 1e-12:
                    aspects.append(max(lengths) / min(lengths))
            neighborhoods = []
            for index in selected:
                reached = {bm.verts[index]}
                frontier = {bm.verts[index]}
                for _ in range(depth):
                    frontier = {edge.other_vert(vertex) for vertex in frontier for edge in vertex.link_edges} - reached
                    reached |= frontier
                neighborhoods.append(
                    {
                        "vertex_index": index,
                        "adjacent_vertex_indices": sorted(vertex.index for vertex in reached if vertex.index != index),
                        "edge_indices": sorted({edge.index for vertex in reached for edge in vertex.link_edges}),
                        "face_indices": sorted({face.index for vertex in reached for face in vertex.link_faces}),
                    }
                )
            detail_records = (
                [
                    {
                        "kind": "boundary_vertex",
                        "loop_index": loop_index,
                        "order": order,
                        "index": vertex_index,
                    }
                    for loop_index, path in enumerate(boundary_paths)
                    for order, vertex_index in enumerate(path["vertex_indices"])
                ]
                + [
                    {"kind": "pole", "valence": int(valence), "index": index}
                    for valence, indices in poles.items()
                    for index in indices
                ]
                + [{"kind": "non_manifold_edge", "index": index} for index in non_manifold]
                + [{"kind": "isolated_vertex", "index": index} for index in isolated_vertices]
                + [{"kind": "isolated_edge", "index": index} for index in isolated_edges]
                + [{"kind": "degenerate_face", "index": index} for index in degenerates]
            )
            start, end, truncated, next_offset = paginate(len(detail_records), offset, limit, _MAX_INSPECT_LIMIT)
            return {
                "name": obj.name,
                "coordinate_space": "LOCAL_BASE_MESH",
                "topology_revision": topology_revision(obj),
                "counts": mesh_counts(obj),
                "evaluated_counts": evaluated_counts,
                "components": {"count": len(components), "vertex_counts": [len(component) for component in components]},
                "boundary_loops": [
                    {
                        "loop_index": loop_index,
                        "closed": path["closed"],
                        "branched": path["branched"],
                        "vertex_count": len(path["vertex_indices"]),
                    }
                    for loop_index, path in enumerate(boundary_paths)
                ],
                "face_types": dict(face_types),
                "poles_by_valence": {valence: len(indices) for valence, indices in poles.items()},
                "diagnostic_counts": {
                    "non_manifold_edges": len(non_manifold),
                    "isolated_vertices": len(isolated_vertices),
                    "isolated_edges": len(isolated_edges),
                    "degenerate_faces": len(degenerates),
                },
                "statistics": {
                    "edge_length": {
                        "min": min(edge_lengths, default=None),
                        "mean": statistics.fmean(edge_lengths) if edge_lengths else None,
                        "max": max(edge_lengths, default=None),
                    },
                    "face_aspect_ratio": {
                        "min": min(aspects, default=None),
                        "mean": statistics.fmean(aspects) if aspects else None,
                        "p95": _percentile(aspects, 95),
                        "max": max(aspects, default=None),
                    },
                },
                "uv_layers": [layer.name for layer in obj.data.uv_layers],
                "vertex_groups": [{"name": group.name, "index": group.index} for group in obj.vertex_groups],
                "modifiers": [_modifier_snapshot(modifier) for modifier in obj.modifiers],
                "symmetry": _symmetry_summary(bm),
                "selected_adjacency": neighborhoods,
                "details": detail_records[start:end],
                "offset": start,
                "limit": min(max(1, int(limit)), _MAX_INSPECT_LIMIT),
                "returned_count": end - start,
                "total_details": len(detail_records),
                "truncated": truncated,
                "next_offset": next_offset,
            }

    def analyze_surface_conformity(
        self,
        object_name,
        source_object_name,
        sample_vertices=True,
        sample_edge_midpoints=False,
        sample_face_centroids=False,
        max_distance=None,
        worst_limit=20,
        create_heat_map=False,
        attribute_name="retopology_distance",
    ):
        obj = get_mesh_object(object_name)
        source = get_mesh_object(source_object_name)
        if not any((sample_vertices, sample_edge_midpoints, sample_face_centroids)):
            raise ValueError("Enable at least one sample type")
        if max_distance is not None:
            max_distance = _positive(max_distance, "max_distance")
        worst_limit = max(1, min(int(worst_limit), 500))
        tree = _world_bvh(source)
        samples = []
        with _read_bmesh(obj) as bm:
            if sample_vertices:
                samples.extend(("VERTEX", vertex.index, obj.matrix_world @ vertex.co) for vertex in bm.verts)
            if sample_edge_midpoints:
                samples.extend(
                    ("EDGE", edge.index, obj.matrix_world @ ((edge.verts[0].co + edge.verts[1].co) * 0.5))
                    for edge in bm.edges
                )
            if sample_face_centroids:
                samples.extend(("FACE", face.index, obj.matrix_world @ face.calc_center_median()) for face in bm.faces)
        hits = []
        missed = []
        vertex_distances = {}
        for kind, index, point in samples:
            projection = _nearest_projection(tree, point, 0.0, max_distance)
            if projection is None:
                missed.append({"element_type": kind, "index": index})
                continue
            hit, normal, face_index, distance = projection
            signed = float((point - hit).dot(normal)) if normal.length_squared > 0 else None
            record = {
                "element_type": kind,
                "index": index,
                "distance": distance,
                "signed_offset": signed,
                "source_face_index": face_index,
            }
            hits.append(record)
            if kind == "VERTEX":
                vertex_distances[index] = distance
        distances = [record["distance"] for record in hits]
        if create_heat_map:
            if not sample_vertices:
                raise ValueError("create_heat_map requires sample_vertices=True")
            attribute = obj.data.attributes.get(attribute_name)
            if attribute is not None and (attribute.domain != "POINT" or attribute.data_type != "FLOAT"):
                raise ValueError(f"Attribute '{attribute_name}' exists but is not a POINT/FLOAT attribute")
            if attribute is None:
                attribute = obj.data.attributes.new(name=attribute_name, type="FLOAT", domain="POINT")
            for index, item in enumerate(attribute.data):
                item.value = float(vertex_distances.get(index, -1.0))
        return {
            "name": obj.name,
            "source": source.name,
            "coordinate_space": "WORLD",
            "sample_count": len(samples),
            "hit_count": len(hits),
            "missed_count": len(missed),
            "missed": missed[:worst_limit],
            "statistics": {
                "mean": statistics.fmean(distances) if distances else None,
                "rms": math.sqrt(statistics.fmean(distance * distance for distance in distances))
                if distances
                else None,
                "p50": _percentile(distances, 50),
                "p90": _percentile(distances, 90),
                "p95": _percentile(distances, 95),
                "p99": _percentile(distances, 99),
                "maximum": max(distances, default=None),
            },
            "signed_offset_reliability": "NEAREST_TRIANGLE_NORMAL",
            "worst": sorted(hits, key=lambda item: item["distance"], reverse=True)[:worst_limit],
            "heat_map_attribute": attribute_name if create_heat_map else None,
            "topology_revision": topology_revision(obj),
        }

    def configure_surface_projection(
        self,
        object_name,
        target_object_name,
        modifier_name="RetopologyProjection",
        wrap_method="NEAREST_SURFACEPOINT",
        wrap_mode="ON_SURFACE",
        offset=0.0,
        project_limit=0.0,
        project_axes=(False, False, True),
        positive_direction=True,
        negative_direction=True,
        cull_face="OFF",
        invert_cull=False,
        auxiliary_target_name=None,
        vertex_group="",
        invert_vertex_group=False,
        apply=False,
    ):
        obj = get_mesh_object(object_name)
        target = get_mesh_object(target_object_name)
        if obj == target:
            raise ValueError("target_object_name must differ from object_name")
        if not isinstance(modifier_name, str) or not modifier_name.strip():
            raise ValueError("modifier_name must be a non-empty string")
        wrap_method = str(wrap_method).upper()
        if wrap_method not in {"NEAREST_SURFACEPOINT", "PROJECT", "NEAREST_VERTEX", "TARGET_PROJECT"}:
            raise ValueError("Unsupported wrap_method")
        wrap_mode = str(wrap_mode).upper()
        if wrap_mode not in {"ON_SURFACE", "INSIDE", "OUTSIDE", "OUTSIDE_SURFACE", "ABOVE_SURFACE"}:
            raise ValueError("Unsupported wrap_mode")
        cull_face = str(cull_face).upper()
        if cull_face not in {"OFF", "FRONT", "BACK"}:
            raise ValueError("cull_face must be OFF, FRONT, or BACK")
        if len(project_axes) != 3:
            raise ValueError("project_axes must contain exactly three booleans")
        if not any(project_axes) and wrap_method == "PROJECT":
            raise ValueError("PROJECT wrap_method requires at least one projection axis")
        if vertex_group and obj.vertex_groups.get(vertex_group) is None:
            raise ValueError(f"Vertex group not found on '{obj.name}': {vertex_group}")
        auxiliary = None
        if auxiliary_target_name:
            auxiliary = get_mesh_object(auxiliary_target_name)
            if auxiliary == obj:
                raise ValueError("auxiliary_target_name must differ from object_name")
        modifier = _configure_shrinkwrap_modifier(
            obj,
            target,
            modifier_name,
            wrap_method,
            wrap_mode,
            _finite(offset, "offset"),
            _positive(project_limit, "project_limit", allow_zero=True),
            tuple(bool(value) for value in project_axes),
            bool(positive_direction),
            bool(negative_direction),
            cull_face,
            bool(invert_cull),
            auxiliary,
            vertex_group,
            bool(invert_vertex_group),
        )
        if apply:
            apply_modifier(obj, modifier)
        return {
            "name": obj.name,
            "target": target.name,
            "modifier": None if apply else modifier.name,
            "applied": bool(apply),
            "modifier_order": _modifier_order(obj),
            "topology_revision": topology_revision(obj),
        }

    def project_mesh_elements(
        self,
        object_name,
        source_object_name,
        vertex_indices=None,
        vertex_group=None,
        method="NEAREST",
        direction=(0.0, 0.0, -1.0),
        direction_space="WORLD",
        offset=0.0,
        max_distance=None,
        positive_direction=True,
        negative_direction=False,
        backface_policy="ALLOW",
        preserve_boundary=False,
        preserve_symmetry_axis="NONE",
        symmetry_tolerance=0.0001,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        source = get_mesh_object(source_object_name)
        _require_revision(obj, expected_revision)
        if (vertex_indices is None) == (vertex_group is None):
            raise ValueError("Provide exactly one of vertex_indices or vertex_group")
        indices = _ensure_indices(obj.data.vertices, vertex_indices, "vertex_indices")
        if vertex_group is not None:
            group = obj.vertex_groups.get(vertex_group)
            if group is None:
                raise ValueError(f"Vertex group not found on '{obj.name}': {vertex_group}")
            indices = []
            for vertex in obj.data.vertices:
                with contextlib.suppress(RuntimeError):
                    if group.weight(vertex.index) > 0.0:
                        indices.append(vertex.index)
        if not indices:
            raise ValueError("No vertices were selected for projection")
        method = str(method).upper()
        if method not in {"NEAREST", "RAYCAST"}:
            raise ValueError("method must be NEAREST or RAYCAST")
        direction_space = str(direction_space).upper()
        if direction_space not in {"LOCAL", "WORLD"}:
            raise ValueError("direction_space must be LOCAL or WORLD")
        ray = _vector(direction, "direction", nonzero=method == "RAYCAST")
        if direction_space == "LOCAL":
            ray = obj.matrix_world.to_3x3() @ ray
        if method == "RAYCAST" and not (positive_direction or negative_direction):
            raise ValueError("RAYCAST requires positive_direction and/or negative_direction")
        backface_policy = str(backface_policy).upper()
        if backface_policy not in {"ALLOW", "CULL"}:
            raise ValueError("backface_policy must be ALLOW or CULL")
        if max_distance is not None:
            max_distance = _positive(max_distance, "max_distance")
        symmetry_axis = str(preserve_symmetry_axis).upper()
        if symmetry_axis not in {"NONE", "X", "Y", "Z"}:
            raise ValueError("preserve_symmetry_axis must be NONE, X, Y, or Z")
        symmetry_tolerance = _positive(symmetry_tolerance, "symmetry_tolerance", allow_zero=True)
        tree = _world_bvh(source)
        projected_indices = []
        failed = []
        preserved = []
        with _editable_bmesh(obj) as bm:
            boundary = {vertex.index for vertex in bm.verts if any(edge.is_boundary for edge in vertex.link_edges)}
            inverse = obj.matrix_world.inverted_safe()
            axis_index = "XYZ".find(symmetry_axis)
            for index in indices:
                vertex = bm.verts[index]
                if preserve_boundary and index in boundary:
                    preserved.append(index)
                    continue
                if axis_index >= 0 and abs(vertex.co[axis_index]) <= symmetry_tolerance:
                    preserved.append(index)
                    continue
                world = obj.matrix_world @ vertex.co
                if method == "NEAREST":
                    projection = _nearest_projection(tree, world, offset, max_distance)
                else:
                    projection = _ray_projection(
                        tree,
                        world,
                        ray,
                        max_distance,
                        positive_direction,
                        negative_direction,
                        backface_policy,
                        offset,
                    )
                if projection is None:
                    failed.append(index)
                    continue
                vertex.co = inverse @ projection[0]
                projected_indices.append(index)
        return {
            "name": obj.name,
            "source": source.name,
            "projected_vertex_indices": projected_indices,
            "preserved_vertex_indices": preserved,
            "failed_vertex_indices": failed,
            "topology_revision": topology_revision(obj),
        }

    def manage_retopology_checkpoint(self, action, object_name, checkpoint_name=None, confirm=False):
        action = str(action).upper()
        if action not in {"CREATE", "LIST", "COMPARE", "RESTORE", "DELETE"}:
            raise ValueError("action must be CREATE, LIST, COMPARE, RESTORE, or DELETE")
        obj = get_mesh_object(object_name)
        collection = bpy.data.collections.get(_CHECKPOINT_COLLECTION)
        checkpoints = (
            []
            if collection is None
            else [
                candidate
                for candidate in collection.objects
                if candidate.get(_CHECKPOINT_TARGET) == obj.name and candidate.type == "MESH"
            ]
        )
        if action == "LIST":
            return {
                "name": obj.name,
                "checkpoints": [
                    {
                        "checkpoint_name": checkpoint.get(_CHECKPOINT_LABEL),
                        "backup_object": checkpoint.name,
                        "counts": mesh_counts(checkpoint),
                        "topology_revision": topology_revision(checkpoint),
                    }
                    for checkpoint in sorted(checkpoints, key=lambda item: item.name)
                ],
            }
        if not isinstance(checkpoint_name, str) or not checkpoint_name.strip():
            raise ValueError("checkpoint_name is required for this action")
        checkpoint = next(
            (candidate for candidate in checkpoints if candidate.get(_CHECKPOINT_LABEL) == checkpoint_name), None
        )
        if action == "CREATE":
            if checkpoint is not None:
                raise ValueError(f"Checkpoint '{checkpoint_name}' already exists for '{obj.name}'")
            if collection is None:
                collection = bpy.data.collections.new(_CHECKPOINT_COLLECTION)
                bpy.context.scene.collection.children.link(collection)
                collection.hide_viewport = True
                collection.hide_render = True
            checkpoint = obj.copy()
            checkpoint.data = obj.data.copy()
            checkpoint.name = f"{obj.name}__checkpoint__{checkpoint_name}"
            checkpoint.data.name = f"{checkpoint.name}_Mesh"
            checkpoint[_CHECKPOINT_TARGET] = obj.name
            checkpoint[_CHECKPOINT_LABEL] = checkpoint_name
            checkpoint.hide_viewport = True
            checkpoint.hide_render = True
            checkpoint.hide_select = True
            collection.objects.link(checkpoint)
            return {
                "action": action,
                "name": obj.name,
                "checkpoint_name": checkpoint_name,
                "backup_object": checkpoint.name,
                "counts": mesh_counts(checkpoint),
                "topology_revision": topology_revision(checkpoint),
            }
        if checkpoint is None:
            raise ValueError(f"Checkpoint '{checkpoint_name}' not found for '{obj.name}'")
        if action == "COMPARE":
            current_counts = mesh_counts(obj)
            backup_counts = mesh_counts(checkpoint)
            current_revision = topology_revision(obj)
            backup_revision = topology_revision(checkpoint)
            current_modifiers = [_modifier_snapshot(modifier) for modifier in obj.modifiers]
            backup_modifiers = [_modifier_snapshot(modifier) for modifier in checkpoint.modifiers]
            current_attributes = [
                {"name": attribute.name, "domain": attribute.domain, "data_type": attribute.data_type}
                for attribute in obj.data.attributes
            ]
            backup_attributes = [
                {"name": attribute.name, "domain": attribute.domain, "data_type": attribute.data_type}
                for attribute in checkpoint.data.attributes
            ]
            transform_matches = obj.matrix_world == checkpoint.matrix_world
            return {
                "action": action,
                "name": obj.name,
                "checkpoint_name": checkpoint_name,
                "matches": (
                    current_revision == backup_revision
                    and transform_matches
                    and current_modifiers == backup_modifiers
                    and current_attributes == backup_attributes
                ),
                "current": {
                    "counts": current_counts,
                    "topology_revision": current_revision,
                    "modifiers": current_modifiers,
                    "attributes": current_attributes,
                },
                "checkpoint": {
                    "counts": backup_counts,
                    "topology_revision": backup_revision,
                    "modifiers": backup_modifiers,
                    "attributes": backup_attributes,
                },
                "count_delta": {key: current_counts[key] - backup_counts[key] for key in current_counts},
                "transform_matches": transform_matches,
            }
        if not confirm:
            raise ValueError(f"{action} requires confirm=True because it changes recoverable checkpoint data")
        if action == "DELETE":
            backup_object_name = checkpoint.name
            backup_mesh = checkpoint.data
            bpy.data.objects.remove(checkpoint, do_unlink=True)
            if backup_mesh.users == 0:
                bpy.data.meshes.remove(backup_mesh)
            return {
                "action": action,
                "name": obj.name,
                "checkpoint_name": checkpoint_name,
                "backup_object": backup_object_name,
                "deleted": True,
            }

        previous_mesh = obj.data
        previous_name = previous_mesh.name
        obj.data = checkpoint.data.copy()
        obj.matrix_world = checkpoint.matrix_world.copy()
        for modifier in list(obj.modifiers):
            obj.modifiers.remove(modifier)
        for source_modifier in checkpoint.modifiers:
            destination = obj.modifiers.new(name=source_modifier.name, type=source_modifier.type)
            for prop in source_modifier.bl_rna.properties:
                if prop.identifier in {"rna_type", "name", "type"} or prop.is_readonly:
                    continue
                with contextlib.suppress(Exception):
                    setattr(destination, prop.identifier, getattr(source_modifier, prop.identifier))
        for group in list(obj.vertex_groups):
            obj.vertex_groups.remove(group)
        for source_group in checkpoint.vertex_groups:
            destination_group = obj.vertex_groups.new(name=source_group.name)
            for vertex in checkpoint.data.vertices:
                with contextlib.suppress(RuntimeError):
                    weight = source_group.weight(vertex.index)
                    destination_group.add([vertex.index], weight, "REPLACE")
        if previous_mesh.users == 0:
            bpy.data.meshes.remove(previous_mesh)
        obj.data.name = previous_name
        original_properties = {key: obj[key] for key in obj}
        try:
            for key in list(obj.keys()):
                del obj[key]
            for key in checkpoint:
                if key not in {_CHECKPOINT_TARGET, _CHECKPOINT_LABEL}:
                    obj[key] = checkpoint[key]
        except Exception:
            for key in list(obj.keys()):
                del obj[key]
            for key, value in original_properties.items():
                obj[key] = value
            raise
        return {
            "action": action,
            "name": obj.name,
            "checkpoint_name": checkpoint_name,
            "restored": True,
            "counts": mesh_counts(obj),
            "topology_revision": topology_revision(obj),
            "modifier_order": _modifier_order(obj),
        }

    def build_quad_patch(
        self,
        object_name,
        corners,
        u_segments,
        v_segments,
        source_object_name=None,
        coordinate_space="WORLD",
        interpolation="BILINEAR",
        boundary_u0=None,
        boundary_u1=None,
        boundary_v0=None,
        boundary_v1=None,
        projection_offset=0.0,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        if not isinstance(corners, (list, tuple)) or len(corners) != 4:
            raise ValueError("corners must contain exactly four points ordered [u0v0, u1v0, u1v1, u0v1]")
        corner_vectors = [_vector(point, f"corners[{index}]") for index, point in enumerate(corners)]
        u_segments, v_segments = int(u_segments), int(v_segments)
        if not 1 <= u_segments <= 1000 or not 1 <= v_segments <= 1000:
            raise ValueError("u_segments and v_segments must each be between 1 and 1000")
        if (u_segments + 1) * (v_segments + 1) > 250_000:
            raise ValueError("Requested patch exceeds the 250,000-vertex safety limit")
        coordinate_space = str(coordinate_space).upper()
        if coordinate_space not in {"LOCAL", "WORLD"}:
            raise ValueError("coordinate_space must be LOCAL or WORLD")
        inverse = obj.matrix_world.inverted_safe()
        if coordinate_space == "WORLD":
            corner_vectors = [inverse @ point for point in corner_vectors]
        interpolation = str(interpolation).upper()
        if interpolation not in {"BILINEAR", "COONS"}:
            raise ValueError("interpolation must be BILINEAR or COONS")
        guides = (boundary_u0, boundary_u1, boundary_v0, boundary_v1)
        if interpolation == "COONS" and any(guide is None or len(guide) < 2 for guide in guides):
            raise ValueError("COONS requires boundary_u0, boundary_u1, boundary_v0, and boundary_v1")

        def local_guide(points):
            converted = [_vector(point, "boundary guide") for point in points]
            return [inverse @ point for point in converted] if coordinate_space == "WORLD" else converted

        local_guides = tuple(local_guide(guide) for guide in guides) if interpolation == "COONS" else None
        if local_guides:
            expected = (
                (corner_vectors[0], corner_vectors[3]),
                (corner_vectors[1], corner_vectors[2]),
                (corner_vectors[0], corner_vectors[1]),
                (corner_vectors[3], corner_vectors[2]),
            )
            tolerance = max(1e-6, max((point.length for point in corner_vectors), default=1.0) * 1e-5)
            for guide, endpoints in zip(local_guides, expected, strict=True):
                if (guide[0] - endpoints[0]).length > tolerance or (guide[-1] - endpoints[1]).length > tolerance:
                    raise ValueError("A Coons boundary endpoint does not match its corresponding corner")
            for first_guide, second_guide in ((local_guides[0], local_guides[1]), (local_guides[2], local_guides[3])):
                if any(
                    _segment_distance(first_guide[a], first_guide[a + 1], second_guide[b], second_guide[b + 1])
                    <= tolerance
                    for a in range(len(first_guide) - 1)
                    for b in range(len(second_guide) - 1)
                ):
                    raise ValueError("Opposite Coons boundaries cross or touch")
        else:
            tolerance = max(1e-9, max((point.length for point in corner_vectors), default=1.0) * 1e-7)
            if (
                min(
                    (first - second).length
                    for index, first in enumerate(corner_vectors)
                    for second in corner_vectors[index + 1 :]
                )
                <= tolerance
            ):
                raise ValueError("Patch corners must be distinct")
            c00, c10, c11, c01 = corner_vectors
            if _segment_distance(c00, c10, c11, c01) <= tolerance or _segment_distance(c10, c11, c01, c00) <= tolerance:
                raise ValueError("Patch boundary edges cross")

        def sample_polyline(points, t):
            lengths = [(points[index + 1] - points[index]).length for index in range(len(points) - 1)]
            total = sum(lengths)
            if total <= 1e-12:
                return points[0].copy()
            target = t * total
            walked = 0.0
            for index, length in enumerate(lengths):
                if walked + length >= target or index == len(lengths) - 1:
                    factor = 0.0 if length <= 1e-12 else (target - walked) / length
                    return points[index].lerp(points[index + 1], factor)
                walked += length
            return points[-1].copy()

        created_verts = []
        created_faces = []
        projection_failed = []
        with _editable_bmesh(obj) as bm:
            old_vert_count, old_edge_count, old_face_count = len(bm.verts), len(bm.edges), len(bm.faces)
            grid = []
            c00, c10, c11, c01 = corner_vectors
            for v_index in range(v_segments + 1):
                v = v_index / v_segments
                row = []
                for u_index in range(u_segments + 1):
                    u = u_index / u_segments
                    bilinear = c00 * ((1 - u) * (1 - v)) + c10 * (u * (1 - v)) + c11 * (u * v) + c01 * ((1 - u) * v)
                    if interpolation == "COONS":
                        left = sample_polyline(local_guides[0], v)
                        right = sample_polyline(local_guides[1], v)
                        bottom = sample_polyline(local_guides[2], u)
                        top = sample_polyline(local_guides[3], u)
                        point = left * (1 - u) + right * u + bottom * (1 - v) + top * v - bilinear
                    else:
                        point = bilinear
                    vertex = bm.verts.new(point)
                    row.append(vertex)
                    created_verts.append(vertex)
                grid.append(row)
            for v_index in range(v_segments):
                for u_index in range(u_segments):
                    created_faces.append(
                        bm.faces.new(
                            (
                                grid[v_index][u_index],
                                grid[v_index][u_index + 1],
                                grid[v_index + 1][u_index + 1],
                                grid[v_index + 1][u_index],
                            )
                        )
                    )
            if source_object_name:
                projection_failed = _project_vertices(
                    obj, created_verts, get_mesh_object(source_object_name), projection_offset
                )
            bm.verts.index_update()
            bm.edges.index_update()
            bm.faces.index_update()
            vertex_indices = sorted(vertex.index for vertex in created_verts)
            face_indices = sorted(face.index for face in created_faces)
            edge_indices = sorted(edge.index for edge in bm.edges if edge.index >= old_edge_count)
        return {
            "name": obj.name,
            "created_vertex_indices": vertex_indices,
            "created_edge_indices": edge_indices,
            "created_face_indices": face_indices,
            "failed_projection_vertex_indices": projection_failed,
            "previous_counts": {"vertices": old_vert_count, "edges": old_edge_count, "polygons": old_face_count},
            "counts": mesh_counts(obj),
            "topology_revision": topology_revision(obj),
        }

    def extend_boundary(
        self,
        object_name,
        ordered_boundary_vertex_indices,
        rows=1,
        distance=0.1,
        mode="VERTEX_NORMAL",
        vector=(0.0, 0.0, 1.0),
        guide_points=None,
        source_object_name=None,
        projection_offset=0.0,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        indices = _ensure_indices(
            obj.data.vertices, ordered_boundary_vertex_indices, "ordered_boundary_vertex_indices", required=True
        )
        if len(indices) != len(ordered_boundary_vertex_indices):
            raise ValueError("ordered_boundary_vertex_indices must not contain duplicates")
        if len(indices) < 2:
            raise ValueError("ordered_boundary_vertex_indices must contain at least two vertices")
        rows = int(rows)
        if not 1 <= rows <= 1000:
            raise ValueError("rows must be between 1 and 1000")
        if rows * len(indices) > 250_000:
            raise ValueError("Requested extension exceeds the 250,000-vertex safety limit")
        distance = _positive(distance, "distance")
        mode = str(mode).upper()
        if mode not in {"FIXED_VECTOR", "VERTEX_NORMAL", "GUIDE_DIRECTED", "SURFACE_TANGENT"}:
            raise ValueError("Unsupported extension mode")
        extension_vector = _vector(vector, "vector", nonzero=mode in {"FIXED_VECTOR", "SURFACE_TANGENT"})
        if mode == "GUIDE_DIRECTED":
            if guide_points is None or len(guide_points) != len(indices):
                raise ValueError("GUIDE_DIRECTED requires one world-space guide point per boundary vertex")
            guides = [_vector(point, "guide_points") for point in guide_points]
        else:
            guides = None
        source = get_mesh_object(source_object_name) if source_object_name else None
        source_tree = _world_bvh(source) if source and mode == "SURFACE_TANGENT" else None
        created_verts = []
        created_faces = []
        projection_failed = []
        with _editable_bmesh(obj) as bm:
            boundary = [bm.verts[index] for index in indices]
            for first, second in itertools.pairwise(boundary):
                edge = bm.edges.get((first, second))
                if edge is None or not edge.is_boundary:
                    raise ValueError("Vertices do not form one ordered open manifold boundary")
            if bm.edges.get((boundary[-1], boundary[0])) is not None:
                raise ValueError("extend_boundary requires an open boundary, not a closed loop")
            old_edge_count = len(bm.edges)
            current = boundary
            inverse = obj.matrix_world.inverted_safe()
            for row_index in range(1, rows + 1):
                new_row = []
                for item_index, vertex in enumerate(current):
                    if mode == "FIXED_VECTOR":
                        delta = extension_vector.normalized() * distance
                    elif mode == "VERTEX_NORMAL":
                        normal = (
                            vertex.normal.normalized() if vertex.normal.length_squared else mathutils.Vector((0, 0, 1))
                        )
                        delta = normal * distance
                    elif mode == "GUIDE_DIRECTED":
                        target_local = inverse @ guides[item_index]
                        delta = (target_local - boundary[item_index].co) * (row_index / rows)
                        delta = target_local - vertex.co if row_index == rows else delta / row_index
                    else:
                        world = obj.matrix_world @ vertex.co
                        projection = _nearest_projection(source_tree, world)
                        if projection is None:
                            raise ValueError(f"Could not determine source tangent at boundary vertex {vertex.index}")
                        world_vector = obj.matrix_world.to_3x3() @ extension_vector
                        tangent = world_vector - projection[1] * world_vector.dot(projection[1])
                        if tangent.length <= 1e-12:
                            raise ValueError(
                                f"Extension vector is parallel to the source normal at vertex {vertex.index}"
                            )
                        delta = obj.matrix_world.to_3x3().inverted_safe() @ (tangent.normalized() * distance)
                    new_vertex = bm.verts.new(vertex.co + delta)
                    new_row.append(new_vertex)
                    created_verts.append(new_vertex)
                for index in range(len(current) - 1):
                    created_faces.append(
                        bm.faces.new((current[index], current[index + 1], new_row[index + 1], new_row[index]))
                    )
                current = new_row
            if source:
                projection_failed = _project_vertices(obj, created_verts, source, projection_offset)
            bm.verts.index_update()
            bm.edges.index_update()
            bm.faces.index_update()
            vertex_indices = sorted(vertex.index for vertex in created_verts)
            face_indices = sorted(face.index for face in created_faces)
            edge_indices = sorted(edge.index for edge in bm.edges if edge.index >= old_edge_count)
            new_boundary_indices = [vertex.index for vertex in current]
        return {
            "name": obj.name,
            "created_vertex_indices": vertex_indices,
            "created_edge_indices": edge_indices,
            "created_face_indices": face_indices,
            "failed_projection_vertex_indices": projection_failed,
            "new_boundary_vertex_indices": new_boundary_indices,
            "counts": mesh_counts(obj),
            "topology_revision": topology_revision(obj),
        }

    def mesh_bridge(
        self,
        object_name,
        loop_a_edge_indices=None,
        loop_b_edge_indices=None,
        edge_indices=None,
        cuts=0,
        interpolation="LINEAR",
        smoothness=0.0,
        twist_offset=0,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        if edge_indices is not None:
            if loop_a_edge_indices is not None or loop_b_edge_indices is not None:
                raise ValueError("Use either legacy edge_indices or the two separate loop inputs, not both")
            legacy = _ensure_indices(obj.data.edges, edge_indices, "edge_indices", required=True)
            with _read_bmesh(obj) as bm:
                selected = {bm.edges[index] for index in legacy}
                components = []
                while selected:
                    seed = selected.pop()
                    component = {seed}
                    queue = [seed]
                    while queue:
                        current = queue.pop()
                        linked = {
                            linked_edge
                            for vertex in current.verts
                            for linked_edge in vertex.link_edges
                            if linked_edge in selected
                        }
                        selected -= linked
                        component |= linked
                        queue.extend(linked)
                    components.append(sorted(item.index for item in component))
            if len(components) != 2:
                raise ValueError("Legacy edge_indices must contain exactly two disconnected boundary loops")
            loop_a_edge_indices, loop_b_edge_indices = components
        first = _ensure_indices(obj.data.edges, loop_a_edge_indices, "loop_a_edge_indices", required=True)
        second = _ensure_indices(obj.data.edges, loop_b_edge_indices, "loop_b_edge_indices", required=True)
        if len(first) != len(loop_a_edge_indices) or len(second) != len(loop_b_edge_indices):
            raise ValueError("Bridge loop inputs must not contain duplicate edge indices")

        def validate_loop(bm, indices, label):
            edges = [bm.edges[index] for index in indices]
            if any(not edge.is_boundary for edge in edges):
                raise ValueError(f"{label} contains an edge that is not a manifold boundary")
            for previous, current in itertools.pairwise(edges):
                if not set(previous.verts) & set(current.verts):
                    raise ValueError(f"{label} is not ordered: consecutive edges do not share a vertex")
            degrees = Counter(vertex for edge in edges for vertex in edge.verts)
            if any(degree > 2 for degree in degrees.values()):
                raise ValueError(f"{label} branches and is not a single loop/chain")
            endpoints = [vertex for vertex, degree in degrees.items() if degree == 1]
            if len(endpoints) not in {0, 2}:
                raise ValueError(f"{label} is neither a closed loop nor an open chain")
            if not endpoints and not (set(edges[-1].verts) & set(edges[0].verts)):
                raise ValueError(f"{label} is closed but the supplied order does not close")
            if len(edges) == 1:
                ordered_vertices = [edges[0].verts[0], edges[0].verts[1]]
            elif endpoints:
                start = next(vertex for vertex in edges[0].verts if vertex not in edges[1].verts)
                ordered_vertices = [start]
                for edge in edges:
                    ordered_vertices.append(edge.other_vert(ordered_vertices[-1]))
            else:
                start = next(vertex for vertex in edges[0].verts if vertex in edges[-1].verts)
                ordered_vertices = [start]
                for edge in edges:
                    ordered_vertices.append(edge.other_vert(ordered_vertices[-1]))
            winding = []
            for edge, start, end in zip(edges, ordered_vertices, ordered_vertices[1:], strict=True):
                face = edge.link_faces[0]
                loop = next(loop for loop in face.loops if loop.edge == edge)
                winding.append(loop.vert == start and loop.link_loop_next.vert == end)
            if len(set(winding)) > 1:
                raise ValueError(f"{label} has inconsistent face winding along its ordered boundary")
            return set(degrees), not endpoints, "FORWARD" if winding[0] else "REVERSED"

        with _read_bmesh(obj) as bm:
            first_vertices, first_closed, first_winding = validate_loop(bm, first, "loop_a_edge_indices")
            second_vertices, second_closed, second_winding = validate_loop(bm, second, "loop_b_edge_indices")
            if first_vertices & second_vertices:
                raise ValueError("Bridge loops may not share vertices")
            if first_closed != second_closed:
                raise ValueError("Both bridge inputs must be open chains or both must be closed loops")
            if any(
                any(vertex in first_vertices for vertex in face.verts)
                and any(vertex in second_vertices for vertex in face.verts)
                for face in bm.faces
            ):
                raise ValueError("The two boundaries already have faces spanning between them")
        cuts = int(cuts)
        if not 0 <= cuts <= 1000:
            raise ValueError("cuts must be between 0 and 1000")
        interpolation = str(interpolation).upper()
        if interpolation not in {"LINEAR", "PATH", "SURFACE"}:
            raise ValueError("interpolation must be LINEAR, PATH, or SURFACE")
        smoothness = _finite(smoothness, "smoothness")
        if not -1000.0 <= smoothness <= 1000.0:
            raise ValueError("smoothness must be between -1000 and 1000")
        before = mesh_counts(obj)
        with edit_mesh(obj, edge_indices=first + second):
            result = bpy.ops.mesh.bridge_edge_loops(
                type="SINGLE",
                twist_offset=int(twist_offset),
                number_cuts=cuts,
                interpolation=interpolation,
                smoothness=smoothness,
            )
            if "FINISHED" not in result:
                raise RuntimeError(f"mesh.bridge_edge_loops did not finish (status: {result})")
        after = mesh_counts(obj)
        return {
            "name": obj.name,
            **after,
            "created_vertex_indices": list(range(before["vertices"], after["vertices"])),
            "created_edge_indices": list(range(before["edges"], after["edges"])),
            "created_face_indices": list(range(before["polygons"], after["polygons"])),
            "input_winding": {"loop_a": first_winding, "loop_b": second_winding},
            "topology_revision": topology_revision(obj),
        }

    def fill_boundary_quads(
        self,
        object_name,
        boundary_edge_indices,
        span=1,
        offset=0,
        use_interp_simple=False,
        source_object_name=None,
        projection_offset=0.0,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        indices = _ensure_indices(obj.data.edges, boundary_edge_indices, "boundary_edge_indices", required=True)
        if len(indices) != len(boundary_edge_indices):
            raise ValueError("boundary_edge_indices must not contain duplicates")
        span = int(span)
        offset = int(offset)
        if not 1 <= span <= 1000:
            raise ValueError("span must be between 1 and 1000")
        if not -1000 <= offset <= 1000:
            raise ValueError("offset must be between -1000 and 1000")
        with _read_bmesh(obj) as bm:
            edges = [bm.edges[index] for index in indices]
            if any(not edge.is_boundary for edge in edges):
                raise ValueError("Every selected edge must be a manifold boundary edge")
            degrees = Counter(vertex for edge in edges for vertex in edge.verts)
            if any(degree != 2 for degree in degrees.values()):
                raise ValueError("boundary_edge_indices must form exactly one closed, non-branching boundary")
            if len(degrees) % 2:
                raise ValueError("Grid Fill requires an even boundary vertex count")
            reached = set()
            queue = [edges[0]]
            edge_set = set(edges)
            while queue:
                edge = queue.pop()
                if edge in reached:
                    continue
                reached.add(edge)
                queue.extend(link for vertex in edge.verts for link in vertex.link_edges if link in edge_set)
            if len(reached) != len(edges):
                raise ValueError("boundary_edge_indices contains more than one boundary")
        before = mesh_counts(obj)
        with edit_mesh(obj, edge_indices=indices):
            result = bpy.ops.mesh.fill_grid(span=span, offset=offset, use_interp_simple=bool(use_interp_simple))
            if "FINISHED" not in result:
                raise RuntimeError(f"mesh.fill_grid did not finish (status: {result})")
        after = mesh_counts(obj)
        new_faces = list(range(before["polygons"], after["polygons"]))
        if any(len(obj.data.polygons[index].vertices) != 4 for index in new_faces):
            raise RuntimeError("Grid Fill produced a non-quad face; the complete edit was rolled back")
        new_vertices = list(range(before["vertices"], after["vertices"]))
        projection_failed = []
        if source_object_name and new_vertices:
            source = get_mesh_object(source_object_name)
            with _editable_bmesh(obj) as bm:
                projection_failed = _project_vertices(
                    obj, [bm.verts[index] for index in new_vertices], source, projection_offset
                )
        return {
            "name": obj.name,
            **mesh_counts(obj),
            "created_vertex_indices": new_vertices,
            "created_edge_indices": list(range(before["edges"], after["edges"])),
            "created_face_indices": new_faces,
            "failed_projection_vertex_indices": projection_failed,
            "topology_revision": topology_revision(obj),
        }

    @staticmethod
    def _perform_reroute(bm, action, vertex_indices, edge_indices, cuts):
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        vertices = [bm.verts[index] for index in vertex_indices]
        edges = [bm.edges[index] for index in edge_indices]
        if action == "CONNECT":
            if len(vertices) != 2:
                raise ValueError("CONNECT requires exactly two vertex_indices")
            if bm.edges.get(vertices) is not None:
                raise ValueError("CONNECT vertices already share an edge")
            return bmesh.ops.connect_vert_pair(bm, verts=vertices)
        if action == "ROTATE_DIAGONAL":
            if (
                len(edges) != 1
                or len(edges[0].link_faces) != 2
                or any(len(face.verts) != 3 for face in edges[0].link_faces)
            ):
                raise ValueError("ROTATE_DIAGONAL requires one interior edge shared by exactly two triangles")
            return bmesh.ops.rotate_edges(bm, edges=edges, use_ccw=False)
        if action == "COLLAPSE":
            if not edges:
                raise ValueError("COLLAPSE requires edge_indices")
            return bmesh.ops.collapse(bm, edges=edges, uvs=True)
        if action == "DISSOLVE":
            if not edges:
                raise ValueError("DISSOLVE requires edge_indices")
            return bmesh.ops.dissolve_edges(bm, edges=edges, use_verts=False, use_face_split=False)
        if action == "SPLIT":
            if not edges:
                raise ValueError("SPLIT requires edge_indices")
            if not 1 <= cuts <= 1000:
                raise ValueError("cuts must be between 1 and 1000")
            return bmesh.ops.subdivide_edges(bm, edges=edges, cuts=cuts, use_grid_fill=False)
        raise ValueError("action must be CONNECT, ROTATE_DIAGONAL, COLLAPSE, DISSOLVE, or SPLIT")

    @staticmethod
    def _validate_reroute_result(bm):
        if any(len(edge.link_faces) > 2 for edge in bm.edges):
            raise ValueError("Action would create a non-manifold edge with more than two incident faces")
        seen_faces = set()
        for face in bm.faces:
            key = frozenset(vertex.index for vertex in face.verts)
            if key in seen_faces:
                raise ValueError("Action would create duplicate faces")
            seen_faces.add(key)
            if len(key) < 3 or face.calc_area() <= 1e-12:
                raise ValueError("Action would create a degenerate face")

    def reroute_topology(
        self,
        object_name,
        action,
        vertex_indices=None,
        edge_indices=None,
        cuts=1,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        vertices = _ensure_indices(obj.data.vertices, vertex_indices, "vertex_indices")
        edges = _ensure_indices(obj.data.edges, edge_indices, "edge_indices")
        action = str(action).upper()
        cuts = int(cuts)
        simulation = bmesh.new()
        try:
            simulation.from_mesh(obj.data)
            simulation.verts.ensure_lookup_table()
            simulation.edges.ensure_lookup_table()
            original_vertices = set(simulation.verts)
            boundary_before = {
                vertex for vertex in simulation.verts if any(edge.is_boundary for edge in vertex.link_edges)
            }
            selected_vertices = {simulation.verts[index] for index in vertices}
            selected_edges = {simulation.edges[index] for index in edges}
            affected_vertices = selected_vertices | {vertex for edge in selected_edges for vertex in edge.verts}
            affected_vertices |= {
                vertex for edge in selected_edges for face in edge.link_faces for vertex in face.verts
            }
            selected_boundary = any(edge.is_boundary for edge in selected_edges)
            self._perform_reroute(simulation, action, vertices, edges, cuts)
            simulation.verts.index_update()
            simulation.edges.index_update()
            simulation.faces.index_update()
            self._validate_reroute_result(simulation)
            boundary_after = {
                vertex for vertex in simulation.verts if any(edge.is_boundary for edge in vertex.link_edges)
            }
            unintended_existing = (boundary_after & original_vertices) - boundary_before - affected_vertices
            unintended_new = boundary_after - original_vertices
            if unintended_existing or (unintended_new and not (action == "SPLIT" and selected_boundary)):
                raise ValueError("Action would create an unintended boundary outside the selected neighborhood")
        finally:
            simulation.free()
        before = mesh_counts(obj)
        with _editable_bmesh(obj) as bm:
            self._perform_reroute(bm, action, vertices, edges, cuts)
            bm.verts.index_update()
            bm.edges.index_update()
            bm.faces.index_update()
            self._validate_reroute_result(bm)
            after_counts = {"vertices": len(bm.verts), "edges": len(bm.edges), "polygons": len(bm.faces)}
        return {
            "name": obj.name,
            "action": action,
            **mesh_counts(obj),
            "created_vertex_indices": list(range(before["vertices"], after_counts["vertices"])),
            "created_edge_indices": list(range(before["edges"], after_counts["edges"])),
            "created_face_indices": list(range(before["polygons"], after_counts["polygons"])),
            "removed_input_vertex_indices": vertices if after_counts["vertices"] < before["vertices"] else [],
            "removed_input_edge_indices": edges if after_counts["edges"] < before["edges"] else [],
            "topology_revision": topology_revision(obj),
        }

    def relax_topology(
        self,
        object_name,
        vertex_indices,
        iterations=3,
        factor=0.5,
        lock_boundary=True,
        lock_vertex_group=None,
        source_object_name=None,
        projection_offset=0.0,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        indices = _ensure_indices(obj.data.vertices, vertex_indices, "vertex_indices", required=True)
        iterations = int(iterations)
        if not 1 <= iterations <= 100:
            raise ValueError("iterations must be between 1 and 100")
        factor = _finite(factor, "factor")
        if not 0.0 <= factor <= 1.0:
            raise ValueError("factor must be between 0 and 1")
        locked = set()
        if lock_vertex_group:
            group = obj.vertex_groups.get(lock_vertex_group)
            if group is None:
                raise ValueError(f"Vertex group not found on '{obj.name}': {lock_vertex_group}")
            for index in indices:
                with contextlib.suppress(RuntimeError):
                    if group.weight(index) > 0.0:
                        locked.add(index)
        source = get_mesh_object(source_object_name) if source_object_name else None
        moved = set(indices) - locked
        projection_failed = set()
        with _editable_bmesh(obj) as bm:
            if lock_boundary:
                locked |= {index for index in indices if any(edge.is_boundary for edge in bm.verts[index].link_edges)}
                moved -= locked
            selected_vertices = [bm.verts[index] for index in indices]
            for _ in range(iterations):
                bm.normal_update()
                updates = {}
                for vertex in selected_vertices:
                    if vertex.index in locked or not vertex.link_edges:
                        continue
                    center = sum(
                        (edge.other_vert(vertex).co for edge in vertex.link_edges), mathutils.Vector((0.0, 0.0, 0.0))
                    ) / len(vertex.link_edges)
                    delta = center - vertex.co
                    normal = vertex.normal.normalized() if vertex.normal.length_squared else mathutils.Vector()
                    tangent = delta - normal * delta.dot(normal)
                    updates[vertex] = vertex.co + tangent * factor
                for vertex, coordinate in updates.items():
                    vertex.co = coordinate
                if source and updates:
                    projection_failed.update(_project_vertices(obj, list(updates), source, projection_offset))
        return {
            "name": obj.name,
            "moved_vertex_indices": sorted(moved),
            "locked_vertex_indices": sorted(locked),
            "iterations": iterations,
            "failed_projection_vertex_indices": sorted(projection_failed),
            "topology_revision": topology_revision(obj),
        }

    def redistribute_edge_loop(
        self,
        object_name,
        loop_vertex_indices,
        closed=False,
        preserve_endpoints=True,
        corner_vertex_indices=None,
        source_object_name=None,
        projection_offset=0.0,
        expected_revision=None,
    ):
        obj = get_mesh_object(object_name)
        _require_revision(obj, expected_revision)
        indices = _ensure_indices(obj.data.vertices, loop_vertex_indices, "loop_vertex_indices", required=True)
        if len(indices) != len(loop_vertex_indices):
            raise ValueError("loop_vertex_indices must not contain duplicates")
        minimum = 3 if closed else 2
        if len(indices) < minimum:
            raise ValueError(f"A {'closed' if closed else 'open'} loop requires at least {minimum} vertices")
        corners = set(_ensure_indices(obj.data.vertices, corner_vertex_indices, "corner_vertex_indices"))
        if not corners.issubset(indices):
            raise ValueError("corner_vertex_indices must be members of loop_vertex_indices")
        source = get_mesh_object(source_object_name) if source_object_name else None
        projection_failed = []

        def resample(points, count, cyclic=False):
            segment_count = len(points) if cyclic else len(points) - 1
            lengths = [(points[(index + 1) % len(points)] - points[index]).length for index in range(segment_count)]
            total = sum(lengths)
            if total <= 1e-12:
                return [point.copy() for point in points]
            targets = [total * index / (count if cyclic else count - 1) for index in range(count)]
            result = []
            for target in targets:
                walked = 0.0
                for index, length in enumerate(lengths):
                    if walked + length >= target or index == len(lengths) - 1:
                        factor = 0.0 if length <= 1e-12 else (target - walked) / length
                        result.append(points[index].lerp(points[(index + 1) % len(points)], factor))
                        break
                    walked += length
            return result

        with _editable_bmesh(obj) as bm:
            vertices = [bm.verts[index] for index in indices]
            pairs = list(itertools.pairwise(vertices))
            if closed:
                pairs.append((vertices[-1], vertices[0]))
            for first, second in pairs:
                if bm.edges.get((first, second)) is None:
                    raise ValueError("loop_vertex_indices are not ordered along existing connected edges")
            original = [vertex.co.copy() for vertex in vertices]
            if not corners:
                coordinates = resample(original, len(original), cyclic=bool(closed))
                for vertex, coordinate in zip(vertices, coordinates, strict=True):
                    vertex.co = coordinate
            else:
                protected_positions = sorted(indices.index(index) for index in corners)
                if not closed:
                    if preserve_endpoints or len(protected_positions) < 2:
                        protected_positions = sorted({0, len(indices) - 1, *protected_positions})
                    for start, end in itertools.pairwise(protected_positions):
                        coordinates = resample(original[start : end + 1], end - start + 1)
                        for position, coordinate in enumerate(coordinates, start):
                            vertices[position].co = coordinate
                elif len(protected_positions) == 1:
                    start = protected_positions[0]
                    rotated_vertices = vertices[start:] + vertices[:start]
                    rotated_points = original[start:] + original[:start]
                    coordinates = resample(rotated_points, len(rotated_points), cyclic=True)
                    for vertex, coordinate in zip(rotated_vertices, coordinates, strict=True):
                        vertex.co = coordinate
                else:
                    positions = [*protected_positions, protected_positions[0] + len(vertices)]
                    extended_vertices = vertices + vertices
                    extended_points = original + original
                    for start, end in itertools.pairwise(positions):
                        coordinates = resample(extended_points[start : end + 1], end - start + 1)
                        for position, coordinate in enumerate(coordinates, start):
                            extended_vertices[position].co = coordinate
            if source:
                projection_failed = _project_vertices(obj, vertices, source, projection_offset)
        return {
            "name": obj.name,
            "redistributed_vertex_indices": indices,
            "preserved_corner_vertex_indices": sorted(corners),
            "closed": bool(closed),
            "failed_projection_vertex_indices": projection_failed,
            "topology_revision": topology_revision(obj),
        }

    def configure_retopology_symmetry(
        self,
        object_name,
        axis="X",
        mirror_object_name=None,
        source_side="POSITIVE",
        bisect=False,
        clipping=True,
        merge=True,
        merge_tolerance=0.001,
        mirror_vertex_groups=True,
        validate_seam=True,
        symmetry_tolerance=0.001,
        modifier_name="RetopologyMirror",
    ):
        obj = get_mesh_object(object_name)
        axis = str(axis).upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("axis must be X, Y, or Z")
        axis_index = "XYZ".index(axis)
        source_side = str(source_side).upper()
        if source_side not in {"POSITIVE", "NEGATIVE"}:
            raise ValueError("source_side must be POSITIVE or NEGATIVE")
        merge_tolerance = _positive(merge_tolerance, "merge_tolerance", allow_zero=True)
        symmetry_tolerance = _positive(symmetry_tolerance, "symmetry_tolerance", allow_zero=True)
        mirror_object = None
        if mirror_object_name:
            mirror_object = bpy.data.objects.get(mirror_object_name)
            if mirror_object is None:
                raise ValueError(f"Mirror object not found: {mirror_object_name}")
            if mirror_object == obj:
                raise ValueError("mirror_object_name must differ from object_name")
        modifier = obj.modifiers.get(modifier_name)
        if modifier is not None and modifier.type != "MIRROR":
            raise ValueError(f"Modifier '{modifier_name}' exists but is type {modifier.type}, not MIRROR")
        if modifier is None:
            modifier = obj.modifiers.new(name=modifier_name, type="MIRROR")
        modifier.use_axis = tuple(index == axis_index for index in range(3))
        modifier.mirror_object = mirror_object
        modifier.use_bisect_axis = tuple(bool(bisect) and index == axis_index for index in range(3))
        # Blender's manual: default bisect keeps the positive side; Flip keeps negative.
        modifier.use_bisect_flip_axis = tuple(
            bool(bisect) and source_side == "NEGATIVE" and index == axis_index for index in range(3)
        )
        modifier.use_clip = bool(clipping)
        modifier.use_mirror_merge = bool(merge)
        modifier.merge_threshold = merge_tolerance
        modifier.use_mirror_vertex_groups = bool(mirror_vertex_groups)
        _move_before_subdivision(obj, modifier)
        unmatched = []
        seam_outside = []
        if validate_seam:
            sync_from_editmode(obj)
            coordinates = []
            if mirror_object:
                plane_inverse = mirror_object.matrix_world.inverted_safe()
                coordinates = [plane_inverse @ (obj.matrix_world @ vertex.co) for vertex in obj.data.vertices]
            else:
                coordinates = [vertex.co.copy() for vertex in obj.data.vertices]
            tree = _kd_tree_class()(len(coordinates))
            for index, coordinate in enumerate(coordinates):
                tree.insert(coordinate, index)
            tree.balance()
            for index, coordinate in enumerate(coordinates):
                mirrored = coordinate.copy()
                mirrored[axis_index] *= -1.0
                _hit, _matched_index, distance = tree.find(mirrored)
                if distance is None or distance > symmetry_tolerance:
                    unmatched.append(index)
                if abs(coordinate[axis_index]) <= merge_tolerance and abs(coordinate[axis_index]) > symmetry_tolerance:
                    seam_outside.append(index)
        return {
            "name": obj.name,
            "modifier": modifier.name,
            "axis": axis,
            "plane_space": "MIRROR_OBJECT_LOCAL" if mirror_object else "OBJECT_LOCAL",
            "source_side": source_side,
            "unmatched_vertex_indices": unmatched,
            "seam_vertices_outside_tolerance": seam_outside,
            "modifier_order": _modifier_order(obj),
            "topology_revision": topology_revision(obj),
        }

    @staticmethod
    def _self_intersections(bm, limit):
        if not bm.faces:
            return []
        tree = _bvh_class().FromBMesh(bm, epsilon=0.0)
        intersections = []
        for first, second in tree.overlap(tree):
            if first >= second:
                continue
            first_face, second_face = bm.faces[first], bm.faces[second]
            if set(first_face.verts) & set(second_face.verts):
                continue
            intersections.append([first, second])
            if len(intersections) >= limit:
                break
        return intersections

    @staticmethod
    def _uv_overlap_pairs(mesh, limit):
        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            return []
        mesh.calc_loop_triangles()
        triangles = []
        for triangle in mesh.loop_triangles:
            coordinates = [uv_layer.data[loop_index].uv.copy() for loop_index in triangle.loops]
            triangles.append((triangle.polygon_index, coordinates))
        if len(triangles) > 2500:
            return None

        def overlap(first, second):
            for triangle in (first, second):
                for index in range(3):
                    edge = triangle[(index + 1) % 3] - triangle[index]
                    axis = mathutils.Vector((-edge.y, edge.x))
                    if axis.length_squared <= 1e-20:
                        return False
                    first_projection = [point.dot(axis) for point in first]
                    second_projection = [point.dot(axis) for point in second]
                    amount = min(max(first_projection), max(second_projection)) - max(
                        min(first_projection), min(second_projection)
                    )
                    if amount <= 1e-10:
                        return False
            return True

        pairs = []
        seen = set()
        for first_index, (first_polygon, first_uvs) in enumerate(triangles):
            for second_polygon, second_uvs in triangles[first_index + 1 :]:
                if first_polygon == second_polygon:
                    continue
                pair = tuple(sorted((first_polygon, second_polygon)))
                if pair in seen:
                    continue
                if overlap(first_uvs, second_uvs):
                    seen.add(pair)
                    pairs.append(list(pair))
                    if len(pairs) >= limit:
                        return pairs
        return pairs

    def validate_retopology(
        self,
        object_name,
        profile="CHARACTER",
        source_object_name=None,
        thresholds=None,
        check_self_intersections=True,
        check_uv_overlap=True,
        check_skin_weights=True,
        issue_limit=100,
    ):
        obj = get_mesh_object(object_name)
        profile = str(profile).upper()
        profiles = {
            "CHARACTER": {
                "max_aspect": 6.0,
                "max_pole_valence": 6.0,
                "max_density_ratio": 4.0,
                "max_triangle_fraction": 0.1,
                "max_ngon_fraction": 0.02,
                "max_conformity": 0.01,
                "double_distance": 1e-5,
                "symmetry_tolerance": 0.001,
            },
            "HARD_SURFACE": {
                "max_aspect": 12.0,
                "max_pole_valence": 8.0,
                "max_density_ratio": 8.0,
                "max_triangle_fraction": 0.25,
                "max_ngon_fraction": 0.1,
                "max_conformity": 0.005,
                "double_distance": 1e-6,
                "symmetry_tolerance": 0.0001,
            },
            "VFX": {
                "max_aspect": 8.0,
                "max_pole_valence": 8.0,
                "max_density_ratio": 5.0,
                "max_triangle_fraction": 0.1,
                "max_ngon_fraction": 0.02,
                "max_conformity": 0.0025,
                "double_distance": 1e-6,
                "symmetry_tolerance": 0.0001,
            },
            "GAME": {
                "max_aspect": 10.0,
                "max_pole_valence": 8.0,
                "max_density_ratio": 8.0,
                "max_triangle_fraction": 0.5,
                "max_ngon_fraction": 0.1,
                "max_conformity": 0.02,
                "double_distance": 1e-5,
                "symmetry_tolerance": 0.001,
            },
        }
        if profile not in profiles:
            raise ValueError(f"profile must be one of {sorted(profiles)}")
        active_thresholds = dict(profiles[profile])
        if thresholds:
            unknown = set(thresholds) - set(active_thresholds)
            if unknown:
                raise ValueError(f"Unknown threshold names: {sorted(unknown)}")
            for name, value in thresholds.items():
                active_thresholds[name] = _positive(value, f"thresholds.{name}", allow_zero=True)
        issue_limit = max(1, min(int(issue_limit), 1000))
        checks = []

        def add_check(name, status, summary, elements=None):
            checks.append(
                {
                    "name": name,
                    "status": status,
                    "summary": summary,
                    "element_indices": list(elements or [])[:issue_limit],
                    "truncated": len(elements or []) > issue_limit,
                }
            )

        temporary = obj.data.copy()
        try:
            validation_changed = temporary.validate(verbose=False, clean_customdata=False)
        finally:
            bpy.data.meshes.remove(temporary)
        add_check(
            "mesh_validate_copy",
            "FAIL" if validation_changed else "PASS",
            "Mesh.validate() found correctable invalid data on a disposable copy"
            if validation_changed
            else "No invalid mesh data found",
        )
        with _read_bmesh(obj) as bm:
            non_manifold = [edge.index for edge in bm.edges if len(edge.link_faces) > 2]
            boundaries = [edge.index for edge in bm.edges if edge.is_boundary]
            degenerates = [face.index for face in bm.faces if face.calc_area() <= 1e-12]
            winding = [edge.index for edge in bm.edges if edge.is_manifold and not edge.is_contiguous]
            isolated = [vertex.index for vertex in bm.verts if not vertex.link_edges]
            aspects = {}
            for face in bm.faces:
                lengths = [edge.calc_length() for edge in face.edges]
                if lengths and min(lengths) > 1e-12:
                    aspects[face.index] = max(lengths) / min(lengths)
            poor_aspect = [index for index, value in aspects.items() if value > active_thresholds["max_aspect"]]
            high_poles = [
                vertex.index for vertex in bm.verts if len(vertex.link_edges) > active_thresholds["max_pole_valence"]
            ]
            face_total = max(1, len(bm.faces))
            triangle_indices = [face.index for face in bm.faces if len(face.verts) == 3]
            ngon_indices = [face.index for face in bm.faces if len(face.verts) > 4]
            density_edges = []
            for edge in bm.edges:
                if len(edge.link_faces) != 2:
                    continue
                first_area, second_area = (face.calc_area() for face in edge.link_faces)
                if min(first_area, second_area) <= 1e-12:
                    continue
                if max(first_area, second_area) / min(first_area, second_area) > active_thresholds["max_density_ratio"]:
                    density_edges.append(edge.index)
            add_check(
                "non_manifold",
                "FAIL" if non_manifold else "PASS",
                f"{len(non_manifold)} edges have more than two faces",
                non_manifold,
            )
            boundary_status = "WARN" if boundaries else "PASS"
            add_check(
                "boundaries",
                boundary_status,
                f"{len(boundaries)} boundary edges; open retopology patches may intentionally retain them",
                boundaries,
            )
            add_check(
                "degenerate_faces",
                "FAIL" if degenerates else "PASS",
                f"{len(degenerates)} zero-area faces",
                degenerates,
            )
            add_check(
                "winding",
                "FAIL" if winding else "PASS",
                f"{len(winding)} manifold edges have inconsistent winding",
                winding,
            )
            add_check(
                "isolated_vertices", "WARN" if isolated else "PASS", f"{len(isolated)} isolated vertices", isolated
            )
            add_check(
                "face_aspect",
                "WARN" if poor_aspect else "PASS",
                f"{len(poor_aspect)} faces exceed aspect {active_thresholds['max_aspect']}",
                poor_aspect,
            )
            add_check(
                "poles",
                "WARN" if high_poles else "PASS",
                f"{len(high_poles)} vertices exceed valence {active_thresholds['max_pole_valence']}",
                high_poles,
            )
            triangle_fraction = len(triangle_indices) / face_total
            ngon_fraction = len(ngon_indices) / face_total
            add_check(
                "face_composition",
                "WARN"
                if triangle_fraction > active_thresholds["max_triangle_fraction"]
                or ngon_fraction > active_thresholds["max_ngon_fraction"]
                else "PASS",
                f"Triangle fraction {triangle_fraction:.4f} (limit {active_thresholds['max_triangle_fraction']}), "
                f"n-gon fraction {ngon_fraction:.4f} (limit {active_thresholds['max_ngon_fraction']})",
                triangle_indices + ngon_indices,
            )
            add_check(
                "density_changes",
                "WARN" if density_edges else "PASS",
                f"{len(density_edges)} adjacent face pairs exceed area ratio {active_thresholds['max_density_ratio']}",
                density_edges,
            )
            tree = _kd_tree_class()(len(bm.verts))
            for vertex in bm.verts:
                tree.insert(vertex.co, vertex.index)
            tree.balance()
            doubles = set()
            for vertex in bm.verts:
                for _coordinate, other_index, _distance in tree.find_range(
                    vertex.co, active_thresholds["double_distance"]
                ):
                    if other_index != vertex.index:
                        doubles.add(max(vertex.index, other_index))
            add_check(
                "doubles", "FAIL" if doubles else "PASS", f"{len(doubles)} near-duplicate vertices", sorted(doubles)
            )
            if check_self_intersections:
                intersections = self._self_intersections(bm, issue_limit)
                add_check(
                    "self_intersections",
                    "FAIL" if intersections else "PASS",
                    f"{len(intersections)} non-adjacent intersecting face pairs found within report limit",
                    intersections,
                )
        uv_layers = list(obj.data.uv_layers)
        if not uv_layers:
            add_check("uv_layers", "WARN", "No UV layer exists")
        elif check_uv_overlap:
            uv_overlaps = self._uv_overlap_pairs(obj.data, issue_limit)
            if uv_overlaps is None:
                add_check(
                    "uv_overlap",
                    "WARN",
                    "UV overlap skipped because the triangulated mesh exceeds the 2,500-triangle safety limit",
                )
            else:
                add_check(
                    "uv_overlap",
                    "WARN" if uv_overlaps else "PASS",
                    f"{len(uv_overlaps)} positive-area UV face overlaps found within report limit",
                    uv_overlaps,
                )
        else:
            add_check("uv_layers", "PASS", f"{len(uv_layers)} UV layer(s) present")
        if check_skin_weights:
            unweighted = []
            unnormalized = []
            deform_group_names = set()
            for modifier in obj.modifiers:
                if modifier.type == "ARMATURE" and modifier.object and modifier.object.type == "ARMATURE":
                    deform_group_names.update(bone.name for bone in modifier.object.data.bones if bone.use_deform)
            deform_groups = {group.index for group in obj.vertex_groups if group.name in deform_group_names}
            for vertex in obj.data.vertices:
                total = sum(item.weight for item in vertex.groups if item.group in deform_groups)
                if deform_groups and total <= 1e-8:
                    unweighted.append(vertex.index)
                elif deform_groups and abs(total - 1.0) > 1e-3:
                    unnormalized.append(vertex.index)
            add_check(
                "skin_weights",
                "WARN" if unweighted or unnormalized else "PASS",
                f"{len(unweighted)} unweighted and {len(unnormalized)} non-normalized vertices",
                unweighted + unnormalized,
            )
        modifier_order = _modifier_order(obj)
        subdivision_indices = [item["index"] for item in modifier_order if item["type"] == "SUBSURF"]
        projection_indices = [item["index"] for item in modifier_order if item["type"] in {"SHRINKWRAP", "MIRROR"}]
        bad_order = bool(
            subdivision_indices and projection_indices and max(projection_indices) > min(subdivision_indices)
        )
        unready_modifiers = [
            modifier.name
            for modifier in obj.modifiers
            if (modifier.type == "SHRINKWRAP" and modifier.target is None)
            or (modifier.type == "MIRROR" and not any(modifier.use_axis))
        ]
        if unready_modifiers:
            modifier_summary = f"Unready modifiers: {unready_modifiers}"
        elif bad_order:
            modifier_summary = "Mirror/Shrinkwrap must precede Subdivision"
        else:
            modifier_summary = "Modifier order is ready"
        add_check(
            "modifier_readiness",
            "FAIL" if bad_order or unready_modifiers else "PASS",
            modifier_summary,
        )
        mirror_modifier = next((modifier for modifier in obj.modifiers if modifier.type == "MIRROR"), None)
        if mirror_modifier:
            axes = [index for index, enabled in enumerate(mirror_modifier.use_axis) if enabled]
            unmatched = []
            if axes:
                coordinates = (
                    [
                        mirror_modifier.mirror_object.matrix_world.inverted_safe() @ (obj.matrix_world @ vertex.co)
                        for vertex in obj.data.vertices
                    ]
                    if mirror_modifier.mirror_object
                    else [vertex.co.copy() for vertex in obj.data.vertices]
                )
                tree = _kd_tree_class()(len(coordinates))
                for index, coordinate in enumerate(coordinates):
                    tree.insert(coordinate, index)
                tree.balance()
                for index, coordinate in enumerate(coordinates):
                    mirrored = coordinate.copy()
                    for axis_index in axes:
                        mirrored[axis_index] *= -1.0
                    _hit, _other, distance = tree.find(mirrored)
                    if distance is None or distance > active_thresholds["symmetry_tolerance"]:
                        unmatched.append(index)
            add_check(
                "symmetry",
                "WARN" if unmatched else "PASS",
                f"{len(unmatched)} vertices lack a symmetric counterpart",
                unmatched,
            )
        else:
            add_check("symmetry", "PASS", "No live Mirror modifier; symmetry is not required by this target")
        conformity = None
        if source_object_name:
            conformity = self.analyze_surface_conformity(
                object_name,
                source_object_name,
                sample_vertices=True,
                sample_edge_midpoints=True,
                sample_face_centroids=False,
                max_distance=None,
                worst_limit=min(issue_limit, 20),
                create_heat_map=False,
            )
            maximum = conformity["statistics"]["maximum"]
            exceeds = maximum is not None and maximum > active_thresholds["max_conformity"]
            add_check(
                "surface_conformity",
                "WARN" if exceeds else "PASS",
                f"Maximum world-space offset {maximum}; threshold {active_thresholds['max_conformity']}",
            )
        statuses = {check["status"] for check in checks}
        overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
        return {
            "name": obj.name,
            "profile": profile,
            "status": overall,
            "thresholds": active_thresholds,
            "checks": checks,
            "modifier_order": modifier_order,
            "conformity": conformity,
            "topology_revision": topology_revision(obj),
        }
