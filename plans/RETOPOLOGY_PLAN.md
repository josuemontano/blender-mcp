## Recommendation

  Implement the following 28 capabilities in three phases. The highest-value design is a small set of explicit, composable retopology tools
  —not generic Python execution or wrappers around every bpy.ops command.

  Blender’s documentation explicitly warns that automatic remeshers do not create reliable deformation topology. QuadriFlow should be
  treated as a starting mesh; character topology still needs controlled loops, projection, inspection, and deformation testing.

  ### P0 — Core production workflow

  1. create_retopology_target

     Description: Create a clean low-poly target associated with one or more source meshes.

     Implementation: Support empty mesh, single vertex, plane, grid, or duplicated evaluated surface. Create it through
     bpy.data.meshes.new()/BMesh, place it in a named retopology collection, copy the source world transform, and store source-object links
     as custom properties. Optionally create live Mirror and Shrinkwrap modifiers, ordered before Subdivision Surface.

  2. inspect_retopology

     Description: Return the information an agent needs to plan topology without dumping the entire mesh.

     Implementation: Use BMesh and the dependency graph to report components, boundary loops, face types, non-manifold edges, poles by
     valence, isolated elements, degenerate faces, edge-length/aspect-ratio statistics, UV layers, vertex groups, modifiers, symmetry, and
     selected adjacency neighborhoods. Paginate large results and include a topology revision/hash so stale indices can be rejected.

  3. analyze_surface_conformity

     Description: Measure how closely the retopology target follows the high-resolution source.

     Implementation: Build an evaluated-source mathutils.bvhtree.BVHTree; sample target vertices and optionally edge midpoints/face
     centroids in world space. Return mean, RMS, percentile and maximum distance, signed offset where reliable, missed projections, and
     worst element IDs. An optional heat-map attribute may be created only when requested.

  4. manage_retopology_checkpoint

     Description: Create, list, compare, restore, and delete recoverable topology checkpoints.

     Implementation: Copy the mesh datablock into a hidden named collection or dedicated backup registry, retaining transforms, modifiers
     and custom attributes. Restoration must require confirmation. This is important because the current transaction mechanism removes
     newly created datablocks but cannot undo partial edits to an existing mesh.

  5. configure_surface_projection

     Description: Idempotently configure a live Shrinkwrap-based projection relationship.

     Implementation: Manage a named SHRINKWRAP modifier using target, wrap_method, wrap_mode, offset, project_limit, projection axes,
     culling, auxiliary target and vertex-group restriction. Return the exact modifier order and retain it live unless explicitly applied.

  6. project_mesh_elements

     Description: Project explicit vertices or a vertex group onto a source surface without relying on viewport snapping.

     Implementation: Use BVH nearest-point or directional ray casts with explicit coordinate space, offset, maximum distance, positive/
     negative direction and backface policy. Validate all indices before mutation, operate through BMesh, report failed projections, and
     optionally preserve boundary or symmetry-plane vertices.

  7. build_quad_patch

     Description: Create a regular quad patch between supplied corners, boundaries, or guides.

     Implementation: Generate a bilinear or Coons-style grid with explicit U/V segment counts, create faces using BMesh, then project its
     vertices to the source. Reject incompatible or crossing boundaries and return the created vertex/edge/face indices plus the new
     topology revision.

  8. extend_boundary

     Description: Grow one or more quad rows from an ordered open boundary.

     Implementation: Use bmesh.ops.extrude_edge_only or direct BMesh construction. Support fixed vector, vertex-normal, guide-directed, and
     surface-tangent extension; then project the new row to the source. Validate that the input is one ordered manifold boundary.

  9. mesh_bridge — extend existing tool

     Description: Reliably bridge two explicitly identified boundary loops.

     Implementation: Add separate ordered loop inputs, cuts, interpolation, smoothness and twist/correspondence offset. Use
     bmesh.ops.bridge_loops or bpy.ops.mesh.bridge_edge_loops, but prevalidate loop closure, manifold status and winding. Return newly
     created elements instead of only aggregate counts.

  10. fill_boundary_quads

     Description: Fill a compatible hole or patch boundary with a quad grid.

     Implementation: Use bmesh.ops.grid_fill or bpy.ops.mesh.fill_grid, exposing span and offset. Reject unsuitable boundaries instead of
     silently falling back to triangle or n-gon fill; project new vertices after filling.

  11. reroute_topology

     Description: Perform controlled local edge-flow corrections.

     Implementation: Provide a bounded action enum such as CONNECT, ROTATE_DIAGONAL, COLLAPSE, DISSOLVE, and SPLIT. Use
     bmesh.ops.connect_vert_pair, connect_verts, collapse, and dissolve operations. Simulate/prevalidate the local result and reject
     actions that create non-manifold geometry, duplicate faces, or unintended boundaries.

  12. relax_topology

     Description: Smooth a patch tangentially while retaining its shape on the source.

     Implementation: Use adjacency-based Laplacian smoothing or bmesh.ops.smooth_laplacian_vert, lock boundaries/features as requested,
     remove normal-direction motion, and reproject after each bounded iteration. If LoopTools is available, its Relax implementation can be
     optional; native behavior must remain available.

  13. redistribute_edge_loop

     Description: Evenly space vertices along an open or closed loop.

     Implementation: Order the loop topologically, calculate cumulative arc length, resample positions, and reproject. Support preserving
     corners and endpoints. LoopTools Space/Circle may be used only after capability detection.

  14. configure_retopology_symmetry — extend model_mirror and mesh_symmetrize

     Description: Establish and validate a symmetry workflow without accidental center-seam damage.

     Implementation: Add an arbitrary mirror object/plane, bisect options, explicit source side, clipping, merge tolerance, vertex-group
     mirroring, and seam validation. Use KD-tree matching to report unmatched symmetric vertices. Keep Mirror live by default.

  15. validate_retopology

     Description: Produce a pass/warn/fail production report using a selected profile.

     Implementation: Profiles such as CHARACTER, HARD_SURFACE, VFX, and GAME should configure thresholds rather than encode simplistic “all
     quads” rules. Check manifoldness, boundaries, doubles, degenerates, self-intersections, winding, face aspect, density changes, poles,
     symmetry, conformity, UV overlap, skin-weight normalization and modifier readiness. Use BMesh, BVH/KD-tree queries, and
     Mesh.validate() only on a copy.


  ### P1 — Guided construction and asset handoff

  16. create_retopology_guides

     Description: Create named surface-conforming curves for facial loops, joints, panel boundaries, seams, or density transitions.

     Implementation: Accept world-space points or source vertex references, project them with BVH queries, and create Curve objects in a
     dedicated guide collection. Store roles such as EYE_LOOP, MOUTH_LOOP, JOINT_RING, HARD_EDGE, or SEAM; never infer semantic anatomy
     without caller-provided intent.

  17. create_surface_section

     Description: Generate a controlled loop from the intersection of the source and a plane.

     Implementation: Intersect an evaluated temporary mesh with a world-space plane, extract connected polylines, choose the requested
     component, resample to an explicit vertex count, and project the result. This is valuable for limbs, cylindrical hardware, pipes, scan
     sections, and mechanical housings.

  18. set_retopology_features

     Description: Mark seams, sharp edges, subdivision creases and bevel weights coherently.

     Implementation: Accept explicit edge IDs or derive candidates from source dihedral angle, curvature, material boundaries or guides.
     Write the Blender 5.1 mesh attributes used for sharpness/crease/bevel data. Return every changed edge and do not automatically make
     all detected features active.

  19. add_support_loops

     Description: Add Subdivision Surface support loops around selected features.

     Implementation: Generate offset loops deterministically through BMesh or a verified offset_edge_loops_slide invocation. Support width,
     side, clamp and corner policy; preserve projection and verify that the result remains manifold. Keep the Subdivision modifier live for
     inspection.

  20. transfer_mesh_attributes

     Description: Transfer production data from source or prior low-poly geometry to the new topology.

     Implementation: Use DataTransferModifier or bpy.ops.object.data_transfer for weights, UVs, colors, custom normals, seams, creases,
     bevel weights and shading flags. Expose mapping mode, object-transform use, maximum distance, source/destination layers and mix mode.
     Map material indices separately through nearest source faces when requested. Default to a live modifier where supported.

  21. unwrap_retopology_uvs

     Description: Create and validate bake-ready UVs.

     Implementation: Support explicit seam-based Angle Based/Conformal unwrap, followed by bpy.ops.uv.average_islands_scale,
     minimize_stretch, and pack_islands. Report zero-area UVs, overlaps, out-of-range coordinates, island count, stretch and texel-density
     variation. Preserve existing maps unless replacement is explicit.


  22. create_bake_cage

     Description: Create a named, editable cage with topology identical to the low-poly mesh.

     Implementation: Duplicate the low-poly mesh data, displace along averaged normals or a caller-provided vertex-group field, and
     validate topology identity, high-poly enclosure, self-intersections and ray misses. Keep the cage visible but non-rendering in a
     dedicated collection.

  23. bake_retopology_maps

     Description: Bake high-to-low production maps in one validated operation.

     Implementation: Use Cycles and bpy.ops.object.bake with selected-to-active setup. Support normal, displacement, AO, position, diffuse,
     roughness and emission through a map-type enum rather than separate tools. Require valid UVs and an explicit image/output path;
     support cage object, extrusion, ray distance, margin, normal space and channel swizzle. Never overwrite files without explicit
     permission, and restore engine, selection and active-node state.

  24. test_deformation

     Description: Evaluate whether character topology survives representative poses or animation frames.

     Implementation: Evaluate the mesh through the dependency graph at specified frames or temporary bone transforms. Compare edge stretch,
     face-area change, volume change, flipped faces, self-intersections and joint-region conformity. Restore frame and pose state in
     finally; do not insert keyframes unless requested.

  ### P2 — Specialized accelerators (implemented)

  Implementation status (2026-08-29): all four specialized accelerators are registered through typed MCP tools and Blender-main-thread
  handlers. Their implementation includes prevalidation, context restoration, transaction rollback, structured validation results, and
  explicit confirmation for materialized LOD generation. Live Blender 5.1 verification is still required where noted by the test report.

  25. generate_quadriflow_draft

     Description: Create a non-destructive automatic quad-remesh candidate for further editing.

     Implementation: Duplicate the source and call bpy.ops.object.quadriflow_remesh with face-count, ratio or edge-length mode, symmetry,
     sharp/boundary preservation, attribute preservation and deterministic seed. Report data-layer loss and validation results. Never label
     the output animation-ready without deformation checks.

  26. fit_surface_primitive

     Description: Fit a clean plane, cylinder, cone or sphere patch to a rigid source region.

     Implementation: Use selected samples plus PCA/least-squares or bounded RANSAC, generate predictable quad topology, and project it back
     to the source. Return fit residuals and reject ambiguous fits. This is especially useful for hard-surface and VFX scan cleanup.

  27. bind_surface_deformation

     Description: Bind a retopologized or render mesh to an animated proxy/simulation surface.

     Implementation: Configure SurfaceDeformModifier and execute its bind operator with a valid context. Preflight the documented target
     restrictions: no non-manifold edges, concave faces, doubles, or collinear face edges. Expose falloff, strength, vertex group and
     sparse bind, and support explicit unbind.

  28. generate_retopology_lods

     Description: Create validated LOD derivatives from an approved retopology master.

     Implementation: Duplicate the master per level, use constrained Decimate or QuadriFlow according to the asset profile, reproject if
     needed, transfer attributes, preserve naming/collections and run validation per level. Keep source and modifiers intact; applying or
     replacing meshes requires confirmation.

## Shared implementation contract

Every topology-mutating tool should:

- Accept explicit object names and element IDs; never depend on current selection.
- Require the caller’s last-seen topology revision for index-based operations.
- State whether coordinates are local or world space.
- Prevalidate every element before changing the first one.
- Preserve active object, mode, selection, frame, render engine and viewport state.
- Maintain a full mesh-data rollback copy, because newly-created-datablock tracking alone cannot reverse partial BMesh edits.
- Return counts before/after, created/removed element mappings, modifier state, topology revision, warnings and validation failures.
- Prefer BMesh/data APIs. Use operators only when they provide unique functionality and verify {'FINISHED'}.
- Leave Shrinkwrap, Mirror, Data Transfer and Subdivision modifiers live by default.


## What should not become separate tools

Avoid exposing modal or screen-coordinate-dependent Poly Build, Knife, Edge Slide and snapping operations directly. Their functionality
should be implemented deterministically through explicit coordinates and BMesh operations. Also avoid separate tools for every bake pass,
one wrapper per modifier, an “automatic final character retopo” claim, and arbitrary Python as a retopology escape hatch.

Existing mesh_extrude, mesh_subdivide, mesh_bevel, mesh_inset, get_mesh_data, get_viewport_screenshot, and
add_subdivision_surface_modifier should be reused rather than duplicated.
