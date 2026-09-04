## Recommendation

Implement the P0 tools (1–12) first. Together they provide the dependable foundation that the current MCP lacks: discovery, inspection, authoring, parameter control, reuse, evaluated-result inspection, and validation for Geometry Nodes. Add the P1 workflow builders once that graph layer is stable, then P2 support for repeat/simulation zones, caches, realization, and performance analysis.

The existing MCP already covers primitives, direct mesh operations, Mirror, Array, radial Array, Subdivision Surface, Displace, Solidify, destructive Boolean, and ND Boolean workflows. Those should remain the efficient path for simple operations. The new procedural surface should focus on reusable Geometry Nodes systems and should not reproduce every existing modifier as a separate procedural tool.

Geometry Nodes graphs should remain ordinary Blender node groups with explicit interfaces and standard dependencies. The MCP should expose validated, task-level commands rather than require arbitrary Python execution or dozens of one-node-at-a-time calls. Every builder should produce an inspectable node group that artists can continue editing in Blender.

## P0 — Geometry Nodes foundation

### 1. `list_procedural_systems`

**Description:** Inventory the procedural systems already present in the file so an agent can reuse or safely extend them.

**Implementation details:** Return paginated Geometry Nodes modifiers, `GeometryNodeTree` datablocks, node-group users, asset status, library/link status, fake-user state, interface summary, supported geometry types, modifier/tool flags, and tagged MCP ownership. Read `bpy.data.node_groups`, object modifier stacks, `NodesModifier.node_group`, `ID.users`, `ID.library`, `ID.asset_data`, and the Blender 5.1 `GeometryNodeTree.is_modifier`, `is_tool`, `is_type_mesh`, `is_type_curve`, `is_type_pointcloud`, and `is_type_grease_pencil` flags. Include orphaned groups and shared groups explicitly. This tool is read-only and bounded by `limit`/`offset`.

### 2. `get_geometry_node_graph`

**Description:** Inspect one node group and its modifier instances in enough detail to plan precise graph edits.

**Implementation details:** Return group identity and users; interface panels and sockets; node name, label, `bl_idname`, parent frame, location, dimensions, mute state, relevant RNA properties, sockets, typed defaults, links, zones, datablock references, and modifier input overrides. Use `NodeTree.interface.items_tree`, `NodeTree.nodes`, `NodeTree.links`, `Node.inputs`/`outputs`, socket identifiers, and `NodesModifier.bakes`/`node_warnings`. Represent Blender IDs by type and name rather than trying to JSON-serialize them. Support section filters and pagination so large production graphs do not exceed message limits. Do not assume display names are unique socket identities.

### 3. `get_geometry_node_type_info`

**Description:** Discover which Geometry Nodes node types and properties the connected Blender 5.1 runtime actually supports.

**Implementation details:** Accept a search/category filter or exact `bl_idname` and return availability, label/category, creatable status, RNA properties, input/output socket templates where static, and actual sockets after bounded temporary instantiation when sockets are dynamic. Use Blender RNA such as `Node.bl_rna_get_subclass_py`, node `input_template`/`output_template` where implemented, and a disposable `GeometryNodeTree` for verified inspection. Maintain a curated allowlist for graph mutation and dynamically remove unavailable node types. Do not copy a static list from another Blender version or accept arbitrary class names without verification.

### 4. `create_geometry_node_group`

**Description:** Create a reusable, empty Geometry Nodes group with an explicit interface and intended execution role.

**Implementation details:** Use `bpy.data.node_groups.new(name, "GeometryNodeTree")`, set `is_modifier` or `is_tool` and applicable geometry/mode flags, then create `NodeGroupInput`/`NodeGroupOutput` nodes and the requested sockets through `NodeTree.interface.new_socket`. Modifier groups should default to a geometry input/output pass-through. Support interface panels, description, color tag, and collision policy. Tag generated groups with a stable UUID, schema version, and purpose. Reject a tool/modifier configuration that Blender reports as incompatible.

### 5. `attach_geometry_nodes_modifier`

**Description:** Add an existing node group to an explicit object as a live Geometry Nodes modifier, or create and attach a new pass-through group atomically.

**Implementation details:** Validate object type and the node tree’s Blender 5.1 applicability flags before calling `obj.modifiers.new(name=..., type='NODES')` and assigning `NodesModifier.node_group`. Accept an exact stack index, viewport/render visibility, and single-user policy. Initialize exposed inputs by interface identifier, not translated display text. Return base and evaluated geometry summaries, modifier position, group sharing, warnings, and live dependencies. If any step fails, remove both the new modifier and any newly created node group.

### 6. `edit_node_group_interface`

**Description:** Add, update, reorder, group, or explicitly remove exposed sockets and panels on a node group.

**Implementation details:** Use `NodeTreeInterface.new_socket`, `new_panel`, `copy`, `move`, `move_to_parent`, and `remove`. Support geometry, scalar, integer, vector, rotation, color, boolean, string, menu, object, collection, material, image, and other Blender 5.1-supported socket classes only after runtime validation. Configure input/output direction, parent panel, default/min/max values, subtype, description, attribute domain, hide-value, and default-attribute behavior where the concrete interface socket exposes them. Preflight the full patch and identify existing items by stable interface identifier. Removing or changing a socket type can invalidate modifier values and links, so require an explicit migration/removal policy and report affected users.

### 7. `patch_geometry_node_graph`

**Description:** Apply an atomic batch of node, property, socket-value, link, frame, and layout edits to one node group.

**Implementation details:** Accept ordered operations such as `add_node`, `update_node`, `set_input`, `add_link`, `remove_link`, `move_to_frame`, `remove_node`, and `set_active_output`. Prevalidate node types, names, property paths, values, socket directions/types, referenced Blender IDs, and all endpoints before mutation. Create nodes with `node_group.nodes.new(bl_idname)`, set only allowlisted writable RNA properties, assign typed socket defaults, and create links with `node_group.links.new`. Address sockets by identifier plus optional index fallback because labels can repeat and dynamic nodes can change socket layouts. Roll back the complete patch on failure and return a name/identifier map. This single batch tool is preferable to separate add-node, set-input, connect, disconnect, and remove-node tools.

### 8. `set_geometry_nodes_inputs`

**Description:** Set exposed modifier inputs for one object or a validated batch of objects without editing the shared node group.

**Implementation details:** Resolve interface input identifiers from `node_group.interface.items_tree`, validate the socket type, and update the modifier’s ID properties. Support scalar/vector/color/rotation values and object, collection, material, image, or texture datablock references. Where a socket permits attribute mode, expose a typed value-versus-named-attribute choice and use the verified Blender 5.1 modifier property/operator contract, including `bpy.ops.object.geometry_nodes_input_attribute_toggle` only under a controlled context when required. Validate the whole batch first, update the dependency graph, and return normalized values by identifier and display name.

### 9. `manage_geometry_nodes_modifier`

**Description:** Rename, reorder, enable, mute, replace the group on, remove, or explicitly apply one Geometry Nodes modifier.

**Implementation details:** Resolve exact object and modifier names. Use modifier RNA for visibility and naming, `obj.modifiers.move(from_index, to_index)` for ordering, and direct `node_group` assignment for replacement after interface compatibility checks. Removal or `bpy.ops.object.modifier_apply` is destructive and must be an explicit action; application requires a controlled active-object/mode override, a `FINISHED` result, and confirmation when source procedural state would be lost. Default to preserving the live modifier. Return old/new stack order, group users, evaluated bounds/counts, and stale-topology warnings when applied.

### 10. `copy_geometry_node_group`

**Description:** Make a node group or modifier instance independent while preserving its graph, interface, and dependencies.

**Implementation details:** Use `NodeTree.copy()` and assign a collision-safe name. Support copying only the group, copying and reassigning selected modifier instances, or duplicating an object with explicit mesh/action/group sharing policies. Preserve external object/collection/material references by default, but return them so the caller can decide whether to remap them. Generate a new MCP UUID and retain provenance linking the copy to its source. Verify no unintended modifier still references the old group.

### 11. `evaluate_procedural_geometry`

**Description:** Inspect the actual evaluated output of a live procedural system without applying it.

**Implementation details:** Use `context.evaluated_depsgraph_get()`, `object.evaluated_get(depsgraph)`, evaluated bounds, and `Object.to_mesh(...)/to_mesh_clear()` where the result can be represented as a mesh. Return component/type availability, world-space bounds, vertex/edge/face counts, material slots, named attributes with type/domain, instance summary, and warnings at an explicit frame. For instance-heavy scenes, inspect dependency-graph object instances with strict limits rather than realizing them. Always restore the current frame and clear temporary evaluated meshes in `finally`. Document when non-mesh components cannot be fully represented by the public object API.

### 12. `validate_geometry_node_graph`

**Description:** Run a non-mutating production check on a node group, its modifiers, and its evaluated outputs.

**Implementation details:** Detect missing node groups and datablock references, invalid links, duplicate or unstable exposed names, unlinked required geometry outputs, stale modifier input identifiers, unsupported object/group combinations, interface migration damage, missing named attributes, unsafe anonymous-attribute boundaries, domain/type mismatches, unexpected shared groups, library-linked read-only data, empty output, non-finite bounds, extreme topology growth, unapplied scale dependencies, modifier warnings, and missing/invalid bake paths. Combine structural checks with bounded dependency-graph evaluation and `NodesModifier.node_warnings`. Return severity, group, node/socket or modifier, affected user, and remediation. Validation must not claim artistic or geometric correctness solely from a successful evaluation.

## P1 — Production workflow builders

### 13. `create_procedural_scatter`

**Description:** Build a reusable surface or volume scattering system for environments, set dressing, foliage, crowds, hair cards, and VFX elements.

**Implementation details:** Generate a named group around `Distribute Points on Faces` or the available volume-distribution node, `Instance on Points`, `Object Info`/`Collection Info`, `Random Value`, normal/alignment logic, and optional `Join Geometry`/`Realize Instances`. Expose density or distance-min, seed, selection/mask, rotation, scale range, source object/collection, original-geometry passthrough, and realization policy. Preserve instancing by default. Support density masks through named attributes/vertex groups and deterministic seeds. Return the generated node map and estimated instance count.

### 14. `create_curve_generator`

**Description:** Build a curve-driven generator for cables, pipes, hoses, rails, trims, hair guides, vines, roads, and motion-graphics strokes.

**Implementation details:** Use input geometry or an explicit curve object with nodes such as `Resample Curve`, `Trim Curve`, `Set Curve Radius`, `Set Curve Tilt`, a curve-circle or custom profile, and `Curve to Mesh`; optionally instance fittings/end caps and set material. Expose resolution, radius/profile dimensions, cyclic/fill settings, trim range, tilt, cap policy, material, and realization. Keep source curves editable and report local/world-space assumptions. Prefer node construction over generating many disconnected cylinder objects.

### 15. `create_procedural_array`

**Description:** Build a generalized linear, grid, radial, or curve-following instance array when the existing simple Array tools are insufficient.

**Implementation details:** Use `Mesh Line`, `Grid`, curve-to-points, or calculated index fields with `Instance on Points`, `Rotate Instances`, `Translate Instances`, and optional `Realize Instances`. Expose count(s), spacing or extent, axis, pivot, angular span, curve orientation, endpoint policy, object/collection source, seed, and realization. Correctly compose transforms in object space and make the pivot/reference object explicit. Retain the current `model_array` for ordinary one-axis mesh repetition; this tool is for multi-axis, radial, curve, or instanced layouts.

### 16. `create_surface_paneling`

**Description:** Build an editable panel, tile, shingle, facade, scale, or greeble system over mesh faces.

**Implementation details:** Compose nodes such as `Subdivide Mesh`, face/mesh-to-points operations, `Scale Elements`, `Extrude Mesh`, `Inset Faces` if supported in the runtime, `Instance on Points`, `Set Position`, and material-selection nodes. Expose panel size/gap, depth, seed, selection mask, normal offset, source collection, boundary policy, and realization. Preserve panel IDs and useful face attributes through `Store Named Attribute` where necessary. Validate normals, non-manifold inputs, and expected output growth; do not silently voxelize or destructively subdivide the source.

### 17. `create_procedural_boolean`

**Description:** Build a live multi-cutter Boolean node group for parametric hard-surface and architectural workflows.

**Implementation details:** Use `GeometryNodeMeshBoolean` with explicit operation and solver properties supported by Blender 5.1. Accept object or collection cutters through `Object Info`/`Collection Info`, realize cutter instances only where the Boolean requires real geometry, and optionally pass through/debug-display cutters. Expose operation, solver, self-intersection/hole handling when available, material behavior, and cutter collection. Put generated helpers in a dedicated collection and never delete them. The existing `mesh_boolean` remains the destructive one-off option and `nd_boolean` remains the live modifier-based two-object option.

### 18. `create_procedural_deformer`

**Description:** Build a non-destructive field-based deformation system for character secondary forms, hard-surface variation, terrain breakup, and VFX motion.

**Implementation details:** Use `Set Position` driven by combinations of Position, Normal, Noise Texture, Map Range, proximity/raycast nodes, curve parameters, and vector math. Offer verified templates such as noise displacement, taper, twist, bend-along-curve, proximity push, and mask-driven offset while sharing one tool schema. Expose strength, scale, falloff, axis/space, seed, target object, and named-attribute/vertex-group mask. Clearly separate object and world spaces and clamp pathological values. This complements the legacy Texture-backed `add_displace_modifier` with fields, masks, and reusable group inputs.

### 19. `create_volume_generator`

**Description:** Build a procedural volume or fog source for clouds, smoke-like static forms, SDF-style effects, and VFX set extensions.

**Implementation details:** Capability-check Blender 5.1 nodes such as `Mesh to Volume`, `Points to Volume`, `Volume Cube`, volume-info, and volume-to-mesh nodes before use. Expose density, voxel amount/size, radius, threshold, interpolation, material, seed, and input geometry. Keep expensive voxel resolution bounded and return an estimated memory/topology warning. Do not present this as a fluid simulation; dynamic state belongs in simulation zones or Blender’s dedicated simulation systems.

### 20. `manage_named_attributes`

**Description:** Create, inspect, populate, rename, convert, or explicitly remove named geometry attributes used as stable contracts between modeling, shading, and simulation graphs.

**Implementation details:** Use the geometry datablock attribute APIs (`Mesh.attributes`, curve/point-cloud equivalents) and nodes such as `Named Attribute`, `Store Named Attribute`, and `Capture Attribute`. Require exact data type and domain (`POINT`, `EDGE`, `FACE`, `CORNER`, `CURVE`, `INSTANCE`, or runtime-supported alternatives), validate element counts, and use `foreach_get`/`foreach_set` for bounded bulk data. Distinguish persistent named attributes from anonymous fields. Removing or converting an attribute requires an explicit policy and a scan of known node-group consumers.

### 21. `manage_procedural_instances`

**Description:** Inspect and safely change the source, picking, transform, and realization policy of an existing procedural instance system.

**Implementation details:** Resolve tagged builder roles or explicit nodes rather than guessing from labels. Update object/collection sources, separate-children/reset-children behavior, pick-instance/index fields, rotation/scale/translation values, and `Realize Instances` placement through `patch_geometry_node_graph`. Return source dependencies, estimated instance count, nesting depth, and whether downstream nodes force realization. This tool provides a stable task-level contract for scatter and array systems while the generic graph patch tool remains available for custom networks.

### 22. `run_geometry_nodes_tool`

**Description:** Execute an existing Geometry Nodes tool asset on explicit mesh, curve, point-cloud, or Grease Pencil objects and element selections.

**Implementation details:** Verify `GeometryNodeTree.is_tool`, applicable type/mode flags, selection requirements, and input schema. Establish the exact active object, mode, and selected element indices, then invoke the verified Blender 5.1 geometry-node tool operator under a proper `VIEW_3D` override; reject modal or cancelled results. Restore mode, active object, object selection, and element selection in `finally`. Since tools can destructively edit geometry, validate all targets first, scope batches explicitly, and return updated counts plus stale-index warnings. Creation and publication of tool groups belong in `create_geometry_node_group` and `publish_procedural_asset`, avoiding a second graph-authoring API.

### 23. `publish_procedural_asset`

**Description:** Prepare a node group as a reusable, discoverable production asset without silently saving or overwriting an asset library.

**Implementation details:** Validate the interface, organize sockets into panels, set descriptions/defaults/min/max, mark the node group as an asset through the Blender ID asset API, assign catalog UUID only when supplied, and manage preview/tag/author metadata supported by Blender 5.1. Set modifier/tool/type flags consistently and retain schema/version/provenance custom properties. Optionally mark fake user. Writing a `.blend` into an asset library requires a separate explicit path and overwrite authorization; this tool should normally modify only the open file.

## P2 — Advanced iteration, simulation, and delivery

### 24. `create_repeat_zone`

**Description:** Add a correctly paired Repeat Zone for bounded iterative modeling such as fractals, growth, recursive detailing, and repeated transforms.

**Implementation details:** Create `GeometryNodeRepeatInput` and `GeometryNodeRepeatOutput`, pair them with the Blender RNA pairing method, add matching `repeat_items`, and wire iteration/state geometry through an atomic graph patch. Expose a hard maximum iteration count at the MCP boundary and reject unbounded or explosive configurations. Support geometry and verified scalar/vector state types. Return paired-node identities, state-item identifiers, and a complexity estimate.

### 25. `create_simulation_zone`

**Description:** Add a correctly paired Simulation Zone for stateful procedural motion, growth, accumulation, trails, and VFX geometry.

**Implementation details:** Create `GeometryNodeSimulationInput`/`GeometryNodeSimulationOutput`, pair them, and configure matching `state_items` with validated socket types. Accept explicit frame/time-step behavior, initial-state connections, and a bounded internal graph patch. Report state schema, dependencies, cache status, and frame range. Simulation-zone creation should not automatically bake, change the scene frame range, or write cache files.

### 26. `manage_geometry_nodes_bake`

**Description:** Inspect, calculate, bake, pack, unpack, or explicitly delete Geometry Nodes Bake-node and Simulation Zone caches.

**Implementation details:** Inspect `NodesModifier.bakes`, `bake_target`, and `bake_directory`; identify entries by modifier and stable bake ID. Use the Blender 5.1 operators `bpy.ops.object.geometry_node_bake_single`, `geometry_node_bake_delete_single`, `geometry_node_bake_pack_single`, `geometry_node_bake_unpack_single`, or bounded simulation cache operators under a validated context. Require explicit frame range, target, directory, byte/time limits, and overwrite/delete confirmation. Verify operator completion and resulting cache state. External paths must be user-provided, normalized, and cleaned only when the MCP created them.

### 27. `realize_procedural_output`

**Description:** Create a standalone deliverable from evaluated procedural geometry while retaining the live source system by default.

**Implementation details:** For a mesh-compatible output, evaluate through the dependency graph and use `bpy.data.meshes.new_from_object(evaluated_object, preserve_all_data_layers=True, depsgraph=depsgraph)` to create a new mesh object with the source world transform and explicit collection/name. Copy materials and validate retained named attributes, UVs, normals, and vertex groups where the evaluated API supports them. Provide policies for keeping instances, realizing instances in-graph first, or applying the existing modifier only with confirmation. Never overwrite the source by default. Report base versus realized counts, lost components/attributes, and provenance.

### 28. `analyze_procedural_performance`

**Description:** Diagnose likely Geometry Nodes performance and memory problems using bounded evaluation and graph heuristics.

**Implementation details:** Measure controlled dependency-graph evaluation at explicit frames, collect output component/count/bounds data, inspect node/modifier warnings, and flag common blowups: early `Realize Instances`, dense volume conversion, nested instances, high subdivision, large grids/point distributions, Boolean fan-in, high repeat counts, unbaked simulations, and unused heavy branches. Public Blender RNA does not provide trustworthy per-node execution timing for every graph, so report whole-system measurements and heuristics separately. Restore the frame, cap repetitions and samples, and never benchmark by mutating quality settings or baking implicitly.

## Shared implementation contract

All procedural-modeling tools should follow the repository’s production contract:

- Require explicit object, node-group, modifier, collection, frame, and coordinate-space inputs wherever ambiguity matters. Never rely on current selection except inside a controlled Geometry Nodes tool execution.
- Validate every operation in a batch before mutation. Reject missing or linked read-only datablocks, unsupported node types, invalid socket values, duplicate identifiers, incompatible geometry types, non-finite numbers, and unsafe complexity limits clearly.
- Run all `bpy` and dependency-graph work on Blender’s main thread. Prefer RNA/data APIs; use `bpy.ops` only for operations such as modifier application, tool execution, and Geometry Nodes baking that require operators.
- Preserve active object, selection, mode, current frame, and editor context with `try`/`finally`. Use `context.temp_override` for every context-sensitive operator and require `{'FINISHED'}`.
- Keep work non-destructive. Share node groups deliberately, keep instancing live, retain source curves/cutters/control geometry, and create a new realized object for delivery unless the user explicitly requests application.
- Treat interface identifiers as the stable modifier-input contract. Display names are for people and may be duplicated, translated, or renamed.
- Tag generated groups, modifiers, helpers, and realized outputs with a stable UUID, role, schema version, ownership, and provenance. Put controls, sources, and outputs in deliberate collections.
- Wrap mutations in `mutation_transaction`, preflight multi-object work, and explicitly remove newly created nodes, links, modifiers, groups, meshes, objects, attributes, and caches on failure. The current transaction tracks new datablocks but graph edits and external bake files need their own rollback journal.
- Return JSON-serializable results with changed objects/groups/modifiers, interface identifiers, generated node-role map, dependencies, evaluated counts and bounds, retained live state, warnings, verification performed, and stale topology/attribute notices.
- Bound node/link payloads, graph traversal, dependency expansion, evaluated instances, point/voxel counts, repeat iterations, frame samples, bake duration, and external-cache size. Return partial paginated inspection rather than truncating silently.
- Add pure tests for schemas, graph-patch validation, interface migrations, socket conversion, and complexity guards, plus Blender 5.1 runtime tests for modifiers, evaluated geometry, shared/single-user groups, fields, attributes, instancing, zones, tools, baking, and realization.

## Established procedural-modeling practices reflected in the plan

- Expose meaningful high-level controls through a small, organized node-group interface; keep internal implementation details inside nested groups.
- Prefer fields and named attributes over destructive mesh edits, but use named attributes only where a persistent cross-graph or shader contract is required.
- Keep instances unrealized for as long as possible. Realize only before operations that need unique geometry or at an explicit delivery boundary.
- Use deterministic seeds and explicit object/local/world-space conversions so procedural variants are repeatable.
- Separate source geometry, controls, cutters, generated output, and delivery meshes into clearly named collections and preserve their relationships.
- Use scale-independent dimensions where practical, or state when object scale affects spacing, bevels, voxel size, or displacement.
- Keep graph stages readable: input/preparation, generation, deformation, materials/attributes, and output. Frames and node labels should describe intent rather than implementation trivia.
- Preserve material indices, UVs, normals, IDs, and required attributes across topology-changing nodes; validate them at the final evaluated output.
- Provide preview/quality inputs for expensive graphs and reserve final subdivision, voxel density, realization, and baking for delivery.
- Version reusable groups and migrate interfaces deliberately. Editing a shared group can change every asset that uses it.

## What should not become separate tools

- Do not expose arbitrary Geometry Nodes Python or a generic unrestricted RNA setter. The default production surface should remain typed and allowlisted.
- Do not create one MCP tool per Geometry Nodes node type, socket, link direction, or math operation. `get_geometry_node_type_info` plus the atomic graph patch covers custom networks without an unmanageable tool list.
- Do not split add-node, remove-node, connect, disconnect, move, label, and set-input into separate round trips. A validated transactional patch is safer and dramatically more efficient.
- Do not reimplement the existing primitive, Mirror, linear Array, radial Array, Subdivision, Displace, Solidify, mesh Boolean, or ND Boolean tools merely by putting one equivalent node in a group.
- Do not create separate tools for every scatter distribution mode, curve product, array layout, deformer, or panel pattern. Use typed variants within their workflow builder.
- Do not conflate `Realize Instances`, modifier application, cache baking, and creation of a standalone evaluated mesh. They have different data-loss and storage consequences.
- Do not make external `.blend` saving or asset-library overwrite an implicit part of asset publication.
- Do not use screenshots as the only verification. Combine visual review with evaluated counts, bounds, attributes, dependency checks, and warnings.

## Comparable MCP findings

Research snapshot: 2026-08-29. Repository capabilities can change after this date.

### `blender-ai-mcp`

`ahujasid/blender-mcp` provides broad scene inspection, arbitrary Blender code execution, asset integrations, and visual verification, but its public tool surface does not expose a structured Geometry Nodes graph lifecycle. Procedural systems can be authored through `execute_blender_code`, which is flexible but offers no stable node schema, atomic graph patch, interface migration, complexity guard, or evaluated validation. The useful precedent is inspection plus before/after visual verification; production Geometry Nodes authoring should move out of arbitrary scripts.

### `blender-mcp-bridge`

`seehiong/blender-mcp-bridge` has a large task-oriented modeling surface spanning primitives, modifiers, curves, arrays, architecture/MEP systems, 3D-print checks, and repeatable recorded sessions. Its session parameters, arithmetic expressions, branches, and replay are valuable procedural ideas at the command-workflow level. The reviewed surface emphasizes generated objects and modifier/operator workflows rather than a general Geometry Nodes graph API. This plan keeps its strong reproducibility lesson while storing core procedural logic in editable Blender node groups instead of long object-creation command sequences.

### `blender-mcp-pro`

`youichi-uda/blender-mcp-pro` offers the clearest comparable Geometry Nodes surface: status and graph inspection, modifier/group creation, node addition/removal, link creation, socket values, group inputs/outputs, node-type listing, and modifier application. Those operations validate that direct node-tree automation is valuable, but most are fine-grained calls and the implementation reviewed includes Blender 3.x fallbacks and permissive value handling. The strongest additions here are an atomic graph patch, runtime node schemas, stable interface identifiers, modifier-input control, shared-group lifecycle, evaluated validation, workflow builders, zones, bake management, realization, and bounded performance analysis.

### Current repository

This repository has the safer starting point for implementation: focused mesh/model tools, evaluated modifier results, main-thread dispatch, state restoration helpers, transaction tracking, and explicit production guidance. Its procedural gap is substantial: there is no Geometry Nodes tool module, graph inspection, node-group interface management, or bake/asset lifecycle. The new implementation should live in `src/blender_mcp/server/tools/geometry_nodes.py` and `src/blender_mcp/bundled/addon/handlers/geometry_nodes.py`, keep `bpy` confined to the add-on, add inspection commands to the read-only command set, and extend rollback beyond newly created datablocks to in-place node-tree edits.

Across all three comparable MCPs, the central opportunity is not merely “add Geometry Nodes nodes.” It is to provide reliable procedural semantics: discoverable runtime schemas, atomic graph changes, intentional sharing, stable exposed parameters, bounded evaluation, reusable production templates, and safe transitions from live graphs to cached or realized deliverables.

## Sources

### Official Blender 5.1 documentation

- [GeometryNodeTree API](https://docs.blender.org/api/5.1/bpy.types.GeometryNodeTree.html)
- [NodesModifier API](https://docs.blender.org/api/5.1/bpy.types.NodesModifier.html)
- [NodeTree API](https://docs.blender.org/api/5.1/bpy.types.NodeTree.html)
- [NodeTreeInterface API](https://docs.blender.org/api/5.1/bpy.types.NodeTreeInterface.html)
- [NodeTreeInterfaceSocket API](https://docs.blender.org/api/5.1/bpy.types.NodeTreeInterfaceSocket.html)
- [Node API](https://docs.blender.org/api/5.1/bpy.types.Node.html)
- [NodeSocket API](https://docs.blender.org/api/5.1/bpy.types.NodeSocket.html)
- [NodeLinks API](https://docs.blender.org/api/5.1/bpy.types.NodeLinks.html)
- [GeometryNodeSimulationOutput API](https://docs.blender.org/api/5.1/bpy.types.GeometryNodeSimulationOutput.html)
- [GeometryNodeRepeatOutput API](https://docs.blender.org/api/5.1/bpy.types.GeometryNodeRepeatOutput.html)
- [Object operators, including Geometry Nodes bake/cache and modifier application](https://docs.blender.org/api/5.1/bpy.ops.object.html)
- [Object evaluation API](https://docs.blender.org/api/5.1/bpy.types.Object.html)
- [BlendDataMeshes API](https://docs.blender.org/api/5.1/bpy.types.BlendDataMeshes.html)
- [Attribute API](https://docs.blender.org/api/5.1/bpy.types.Attribute.html)
- [Geometry Nodes manual](https://docs.blender.org/manual/en/5.1/modeling/geometry_nodes/)
- [Geometry Nodes modifier](https://docs.blender.org/manual/en/5.1/modeling/modifiers/generate/geometry_nodes.html)
- [Geometry Nodes attributes](https://docs.blender.org/manual/en/5.1/modeling/geometry_nodes/attributes_reference.html)
- [Geometry Nodes instances](https://docs.blender.org/manual/en/5.1/modeling/geometry_nodes/instances.html)
- [Simulation Zone](https://docs.blender.org/manual/en/5.1/modeling/geometry_nodes/simulation/simulation_zone.html)
- [Repeat Zone](https://docs.blender.org/manual/en/5.1/modeling/geometry_nodes/utilities/repeat_zone.html)
- [Asset Libraries](https://docs.blender.org/manual/en/5.1/files/asset_libraries/introduction.html)

### Comparable projects and repository guidance

- [`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) (`blender-ai-mcp`)
- [`seehiong/blender-mcp-bridge`](https://github.com/seehiong/blender-mcp-bridge)
- [`youichi-uda/blender-mcp-pro`](https://github.com/youichi-uda/blender-mcp-pro)
- Repository `AGENTS.md` / `CLAUDE.md` production, safety, transaction, and Blender 5.1 requirements

