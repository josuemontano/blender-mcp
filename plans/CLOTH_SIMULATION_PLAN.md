## Recommendation

Implement the P0 tools (1–12) first. Together they cover the safe foundation of professional cloth work: inspection, cloth creation, material and solver configuration, weighted properties, object and self-collision, collider setup, resource estimation, and validation. Add P1 for sewing, pressure, internal springs, animated rest/attachment workflows, force fields, character garments, sampling, and cache management. Add P2 after live Blender 5.1 testing because simulation proxies, variants, finishing, export, and profiling interact with evaluated geometry and potentially expensive caches.

The current MCP has no dedicated cloth-simulation surface. Existing mesh, vertex-group, modifier, animation, collection, inspection, and screenshot tools remain useful prerequisites, but cloth state is otherwise reachable only through arbitrary Python. The new commands should expose Blender's native cloth system through typed, cache-aware operations, preserve source and render meshes, and make every cache-invalidating change explicit.

## P0 — Core cloth setup

### 1. `get_cloth_simulation_info`

**Description:** Inspect cloth simulations, colliders, force fields, caches, and dependencies in an explicit scene or collection without changing them.

**Implementation details:** Enumerate bounded `CLOTH` and `COLLISION` modifiers and report their owning objects, collection membership, local/world transforms, modifier order, animation, `ClothSettings`, `ClothCollisionSettings`, collider `CollisionSettings`, referenced vertex groups, rest shape keys, effector weights/collections, and `PointCache` state. Include cache frame range, path/mode, baked/baking/outdated flags, solver status, likely cloth-to-collider relationships, and missing references. Paginate objects and large dependency lists. Do not advance the timeline, initialize a bake, or describe a collider as affecting cloth when it is excluded by the cloth collision collection.

### 2. `get_cloth_object_info`

**Description:** Inspect one cloth object or collider in enough detail to plan a safe simulation change.

**Implementation details:** Return object/data type, base and evaluated counts, local/world transforms, dimensions, scale and determinant, mesh topology summary, modifier stack, vertex groups and assignment statistics, shape keys, animation, cloth/collision settings, force-field relationships, and point-cache state. For cloth, expose solver/material, pinning, sewing, pressure, internal springs, property weights, object/self-collision, and field weights. For colliders, expose collision thickness, damping, friction, culling, normal, permeability, and animation. Call `update_from_editmode()` when needed and label base versus evaluated geometry and coordinate spaces.

### 3. `add_cloth_simulation`

**Description:** Add a named Cloth modifier to an explicit mesh and initialize it with safe, inspectable settings.

**Implementation details:** Validate a mesh object, nonempty topology, transform scale, modifier name/order, and absence or replacement policy for an existing Cloth modifier. Create it with `obj.modifiers.new(name=..., type='CLOTH')`, update the view layer if Blender needs evaluation before `settings`, `collision_settings`, or `point_cache` are available, then apply a documented preset or explicit settings through the same validators used by the configuration tools. Set an explicit cache frame range and collision collection where supplied. Tag the modifier/object with simulation ownership metadata, return all retained live dependencies, and remove only changes made by this request on failure.

### 4. `configure_cloth_material`

**Description:** Configure the cloth's mass, stretch, shear, bending, and damping behavior as a coherent material model.

**Implementation details:** Patch verified Blender 5.1 `ClothSettings` fields including `mass`, `air_damping`, `bending_model`, tension/compression/shear/bending stiffness, their maximum values, and tension/compression/shear/bending damping. Validate finite RNA ranges, scene scale, mesh density, and whether weighted maximum values have corresponding groups. Offer versioned starting presets such as cotton, silk, denim, leather, rubber, or heavy canvas only as documented parameter bundles—not claims of real-world calibration. Return old/new values and invalidate the point cache only after full validation and explicit mutation.

### 5. `configure_cloth_solver`

**Description:** Patch simulation quality, time scaling, gravity, and solver-level controls independently of material behavior.

**Implementation details:** Configure `ClothSettings.quality`, `time_scale`, `gravity`, `voxel_cell_size`, and other solver-level fields verified at runtime for Blender 5.1. Keep collision quality in `configure_cloth_collisions`, field multipliers in `configure_cloth_field_weights`, and physical stiffness/damping in `configure_cloth_material`. Validate positive time scale, bounded quality, finite vectors, and the relationship between scene FPS, frame range, object speed, and smallest edge length. Refuse settings changes on a baked cache unless cache freeing is separately authorized, and return estimated substep/cost impact without promising exact solve time.

### 6. `set_cloth_vertex_weights`

**Description:** Create or update exact vertex weights for pinning and spatially varying cloth, pressure, spring, or collision behavior.

**Implementation details:** Accept bounded assignments with a typed role: pin/mass, structural stiffness, shear stiffness, bending stiffness, shrink, pressure, internal springs, object-collision exclusion, or self-collision exclusion. Resolve each role to the corresponding `ClothSettings` or `ClothCollisionSettings` vertex-group field, then edit `Object.vertex_groups` with validated vertex indices and weights in `[0, 1]`. Support replace/add/subtract semantics, preserve locked and unrelated groups, preflight the complete batch, and record old weights for rollback. Return created groups, changed vertices, weight statistics, and a cache-invalidation warning; topology-changing tools require clients to query indices again.

### 7. `configure_cloth_pinning`

**Description:** Configure how a weighted vertex group follows its animated pre-cloth position while the remaining surface simulates.

**Implementation details:** Validate and set `ClothSettings.vertex_group_mass`, `pin_stiffness`, `goal_min`, `goal_max`, `goal_default`, `goal_spring`, and `goal_friction` as supported by Blender 5.1. Require the pin group to exist and report empty or weakly weighted boundaries that may detach or oscillate. Inspect modifier order so Armature, Hook, Mesh Deform, or other animation intended to drive pinned vertices evaluates before Cloth. This tool assigns pin behavior but does not invent weights or change upstream animation; use `set_cloth_vertex_weights` and `create_cloth_attachment` for those operations.

### 8. `configure_cloth_collisions`

**Description:** Configure cloth-side object collision and self-collision, including scope and exclusion masks.

**Implementation details:** Patch `ClothCollisionSettings.use_collision`, `collision_quality`, `distance_min`, `impulse_clamp`, damping/friction fields, collision `collection`, and `vertex_group_object_collisions`; separately patch `use_self_collision`, `self_distance_min`, `self_friction`, `self_impulse_clamp`, and `vertex_group_self_collisions`. Validate positive distances against cloth edge scale and collider thickness, verify referenced collections/groups, and flag settings likely to cause tunneling or explosive separation. Preserve collider configuration and do not silently enable collision on unrelated objects. Return the exact scope and cache invalidation.

### 9. `add_cloth_collider`

**Description:** Enable collision physics on an explicit mesh or curve object and optionally register it with one or more cloth setups.

**Implementation details:** Validate object type, evaluated geometry, transforms, modifier order, animation, and collision-collection policy. Add a named `COLLISION` modifier through the data API when Blender initializes it correctly; otherwise use `bpy.ops.object.modifier_add(type='COLLISION')` under a controlled Object Mode override and require `{'FINISHED'}`. Verify that `Object.collision`/`CollisionSettings.use` is active, then add the object to explicit collision collections without removing its existing links. Set initial values through `configure_cloth_collider`, tag ownership only for MCP-created components, and roll back the modifier and new collection memberships on failure.

### 10. `configure_cloth_collider`

**Description:** Patch collider-side thickness, friction, damping, permeability, and one-sided collision behavior.

**Implementation details:** Update verified `CollisionSettings` fields such as `use`, `thickness_outer`, `thickness_inner`, `cloth_friction`, `damping`, `damping_factor`, `friction_factor`, `absorption`, `permeability`, `stickiness`, `use_culling`, and `use_normal` only when applicable to cloth in Blender 5.1. Validate nonnegative distances and bounded coefficients, normals for one-sided collision, animated/deforming topology, and modifier placement relative to Armature or deformation modifiers. Return old/new settings and every affected cloth cache; do not add rigid-body or fluid behavior.

### 11. `estimate_cloth_resources`

**Description:** Estimate relative solver cost and risky settings before evaluating or baking a cloth simulation.

**Implementation details:** Use cloth vertex/edge/face counts, frame range, solver quality, collision quality, self-collision, collider counts and evaluated complexity, internal springs, pressure, modifier topology changes, and expected subframe motion to produce bounded relative CPU/memory/cache estimates. Include edge-length statistics, likely collision pair pressure, and low/medium/high recommendations for preview and final settings. If runtime cache size or solve timing is available, report it separately from estimates. Do not promise exact memory, disk, or bake duration because contact density, deformation, hardware, and scene dependencies dominate.

### 12. `validate_cloth_setup`

**Description:** Run a non-mutating production preflight over cloth geometry, settings, colliders, force fields, modifier order, and cache state.

**Implementation details:** Detect missing/duplicate Cloth modifiers, zero-area or duplicate geometry, inconsistent normals, extreme edge-length ratios, unsuitable nonuniform/negative scale, animated topology, invalid pin/property groups, all-pinned or unpinned expected regions, pressure on an open/non-manifold surface, malformed sewing edges, self-collision distances larger than local features, absent or high-resolution colliders, cloth initially intersecting colliders, fast motion with insufficient quality, wrong modifier order, shared or stale caches, invalid cache paths, broken force collections, and settings changed after baking. Return severity, object/element/frame/property, evidence, and remediation. Never claim visual or physical correctness without representative evaluated-frame review.

## P1 — Construction, animation, character integration, and caches

### 13. `configure_cloth_sewing`

**Description:** Configure sewing springs across explicit loose edges for garments, tents, sails, and assembled fabric panels.

**Implementation details:** Inspect or create only explicitly requested loose mesh edges, validate endpoint indices, panel boundaries, duplicate edges, seam direction, and pairing distances, then set `ClothSettings.use_sewing_springs` and `sewing_force_max`. Topology edits must occur before baking and require clients to refresh mesh indices. Provide a dry-run that reports seam counts, unmatched boundaries, likely folds, and whether any sewing edge already belongs to a face. Do not infer a garment pattern or sew nearest boundaries automatically without a caller-approved mapping, and do not merge panel vertices because sewing relies on spring-connected separate edges.

### 14. `configure_cloth_pressure`

**Description:** Configure closed-surface pressure for balloons, airbags, cushions, inflatable VFX assets, and soft shells.

**Implementation details:** Validate a consistently oriented, closed manifold mesh and patch `ClothSettings.use_pressure`, `uniform_pressure_force`, `use_pressure_volume`, `target_volume`, `pressure_factor`, `fluid_density`, and `vertex_group_pressure`. Report signed/current volume, boundary edges, inverted faces, and the relationship between pressure and material stiffness before enabling the feature. Support animated pressure through a separate animation tool. Pressure changes invalidate the cloth cache; never seal holes, recalculate normals, or replace the mesh implicitly.

### 15. `configure_cloth_internal_springs`

**Description:** Configure volumetric internal springs when Cloth is used for soft props, flesh proxies, cushions, or squashable VFX objects.

**Implementation details:** Patch `use_internal_springs`, `internal_spring_max_length`, `internal_spring_max_diversion`, `internal_spring_normal_check`, internal tension/compression stiffness and maximum values, internal friction, and `vertex_group_intern` where available in Blender 5.1. Validate manifoldness/normals, density, maximum spring creation distance, and potential quadratic growth in spring count. Return a conservative spring-count/cost estimate and refuse unbounded settings on dense meshes. Keep this distinct from pressure because the mechanisms and failure modes differ.

### 16. `configure_cloth_rest_shape`

**Description:** Set a shape key as the cloth rest shape and control dynamic rest-mesh behavior for animated or morphing cloth.

**Implementation details:** Validate the mesh's `Key` datablock, Basis, target `ShapeKey`, vertex-count/topology identity, and animation before assigning `ClothSettings.rest_shape_key` and `use_dynamic_mesh`. Make clear whether upstream modifier animation, shape-key animation, or the static rest key defines the pre-simulation surface. Dynamic mesh evaluation can be expensive and invalidates cached results, so require an explicit frame range and warn about topology-changing modifiers. Preserve all shape keys and never apply or reorder modifiers automatically.

### 17. `configure_cloth_field_weights`

**Description:** Control gravity and force-field influence on cloth and scope applicable effectors through a collection.

**Implementation details:** Patch `ClothSettings.effector_weights` using `EffectorWeights`: gravity, all/individual field-type multipliers, and the effector collection supported by Blender 5.1. Validate finite bounded values and referenced collections, list matching `FieldSettings` objects, and warn about fields outside the requested scene/view layer or unexpectedly close to the cloth. Treat the cloth-specific gravity vector and effector gravity multiplier as separate inputs and report their combined intent. Do not create force fields in this tool; existing general object tools or a typed field-creation command can do so.

### 18. `animate_cloth_parameters`

**Description:** Keyframe supported cloth controls such as pressure, shrink, pin influence, field strength, or upstream attachments without overwriting unrelated animation.

**Implementation details:** Accept exact property/value/frame records from a curated allowlist and use RNA assignment plus `keyframe_insert()` on the correct owner (`ClothSettings`, `EffectorWeights`, collider object/settings, shape key, vertex-group-driving modifier, or control object). Check each property is animatable in Blender 5.1 before mutation. Support insert-only and replace-existing-key policies plus interpolation updates through `FCurve` data. Never animate raw vertex-group membership per frame; use an upstream modifier, shape key, dynamic-paint workflow, or explicitly animated cloth setting instead. Return actions/curves and cache invalidation.

### 19. `create_cloth_attachment`

**Description:** Create an explicit animated attachment that drives pinned cloth vertices from an object or armature bone.

**Implementation details:** Validate a cloth object, pin vertex group, target object/bone, rest frame, and modifier order. Create or configure a `HOOK`, `ARMATURE`, `MESH_DEFORM`, or `SURFACE_DEFORM` relationship only through typed variants; for a simple attachment, set a Hook modifier's target, subtarget, vertex group, and inverse so the rest pose is preserved, and place it before Cloth. Complex bind operators must run with controlled context and verified completion. Reuse existing controls only when requested, tag generated helpers, and verify that pinned evaluated vertices follow the target without moving the unpinned rest mesh unexpectedly.

### 20. `create_character_cloth_setup`

**Description:** Assemble a non-destructive garment setup around an animated character using explicit cloth, armature, pin, and collision assets.

**Implementation details:** Require named garment, armature, body collider/proxy objects, pin groups, and collection policies rather than inferring anatomy. Verify that the Armature or other character-deformation modifier evaluates before Cloth, configure pinning, add/register collision proxies, and optionally add render-only smoothing/thickness after Cloth. Preserve the original garment and character, do not auto-generate weights or collision proxies without an explicit policy, and never apply the stack. Validate the rest-frame fit, initial intersections, character motion range, armature scale, and ownership; return the complete dependency graph and recommended test frames.

### 21. `sample_cloth_simulation`

**Description:** Evaluate selected frames and return measurable cloth state for iteration before a full bake.

**Implementation details:** Preserve the current frame, set requested frames in ascending order, update the dependency graph, and extract bounded evaluated-mesh evidence: world bounds, vertex displacement and velocity estimates, surface area/volume where meaningful, collider proximity/penetration heuristics, inverted or degenerate faces, and solver-result status. Sampling can populate or invalidate Blender's in-memory point cache, so classify this as cache-affecting evaluation rather than a pure read and disclose the result. Enforce frame/sample/time limits, restore the timeline in `finally`, and never claim stable convergence from a few samples.

### 22. `manage_cloth_cache`

**Description:** Inspect, configure, bake, resume where supported, or explicitly free the point cache of one cloth modifier.

**Implementation details:** Address the exact `ClothModifier.point_cache`; expose status plus `frame_start`, `frame_end`, `frame_step`, cache name/index, `use_disk_cache`, `use_external`, `use_library_path`, and `filepath` as supported. Validate writable explicit paths, unique cache ownership, frame bounds, and dependencies before baking. Use `bpy.ops.ptcache.bake(bake=True)`, `bake_from_cache`, or `free_bake` only with a correct object/scene/point-cache context override and require `{'FINISHED'}`. Never call scene-wide `bake_all`/`free_bake_all`; freeing a bake or replacing external cache files requires explicit confirmation. Long bakes need progress, cancellation, timeout, and truthful partial-state reporting.

### 23. `remove_cloth_components`

**Description:** Remove explicitly selected cloth or collision components while preserving unrelated objects, modifiers, groups, and caches.

**Implementation details:** Support exact targets such as a named Cloth modifier, Collision modifier/settings, MCP-owned attachment helper, or MCP-owned collection membership. Preflight dependencies and report render meshes, drivers, groups, and caches that will become orphaned. Removing a baked modifier or deleting external cache data requires separate confirmation; removing the modifier must not silently delete vertex groups, source meshes, materials, controls, or cache directories. Use ownership metadata and exact identifiers rather than broad selection, and return what was removed and what remains recoverable.

## P2 — Proxy workflows, variants, finishing, and delivery

### 24. `create_cloth_proxy_rig`

**Description:** Drive a high-resolution render garment from a lower-resolution cloth proxy while keeping both meshes editable.

**Implementation details:** Accept explicit simulation and render meshes or create a duplicate/decimated proxy only with permission to alter topology. Add Cloth to the proxy and a `SURFACE_DEFORM` or `MESH_DEFORM` modifier to the render mesh with explicit target and stack position; run the corresponding bind operator under controlled context and verify its success and bind state. Preserve source geometry, UVs, materials, shape keys, and armature dependencies. Validate rest-pose coverage, cage proximity, non-manifold regions, and deformation at representative frames; return the live proxy-to-render relationship and known bind limitations.

### 25. `duplicate_cloth_setup_variant`

**Description:** Duplicate a cloth setup for preview/final, alternate materials, or shot-specific simulation without sharing unsafe cache state.

**Implementation details:** Discover members through simulation UUIDs and explicit dependencies, then preflight names and copy policies for meshes, cloth modifiers/settings, vertex groups, shape keys, colliders, force fields, actions, and render drivers. Use `Object.copy()` and datablock copies deliberately, remap internal references, and give each variant a unique cache name/path and ownership ID. Permit colliders or source animation to remain shared only through explicit policies. Verify that no new cloth modifier points to the original point cache or unintended groups and return a source-to-variant map.

### 26. `prepare_cloth_render_surface`

**Description:** Add a reversible render-finishing stack for thickness, smoothing, subdivision, normals, and materials after cloth deformation.

**Implementation details:** Inspect the current modifier order and add/update named `CORRECTIVE_SMOOTH`, `SUBSURF`, `SOLIDIFY`, and normal-related modifiers only when requested and verified in Blender 5.1. Default to Cloth before smoothing/subdivision and Solidify after simulation so the solver uses the light surface; preserve intentional exceptions and never apply modifiers. Validate thickness against folds/collision distances, subdivision cost, material offsets, UV preservation, and motion-blur requirements. Return base and evaluated counts/bounds plus the exact live stack; surface finishing does not repair an unstable simulation.

### 27. `export_cloth_simulation`

**Description:** Export baked or deterministically evaluated cloth deformation for VFX, rendering, or downstream DCC handoff.

**Implementation details:** Require an explicit path, format, object list, frame range/step, world/local space, units/axes, topology policy, attributes, and overwrite confirmation. Prefer point-varying geometry formats such as Alembic or USD after Blender 5.1 capability verification; use MDD/PC2 only when the relevant add-on/operator is enabled and verified. Bake first through the cache lifecycle or sample a temporary duplicate according to an explicit policy, control selection/context, verify operator completion and the written nonempty file, and preserve source objects/caches. Return provenance, topology stability, frame count, exported attributes, and format limitations.

### 28. `analyze_cloth_performance`

**Description:** Profile representative cloth frames and identify the dominant geometry, collision, solver, or cache costs.

**Implementation details:** Measure bounded cold/warm frame evaluations and, when authorized, a short isolated bake on a duplicate/cache path. Correlate timings and cache growth with cloth vertex/constraint counts, quality, collision quality, self-collision, collider evaluated polygons, pressure/internal springs, modifier topology, and frame-to-frame motion. Read `ClothModifier.solver_result` and point-cache state where useful, distinguish measured values from estimates, and report actionable reductions such as simpler collision proxies, lower preview quality, fewer self-collision vertices, or a proxy rig. Restore the frame and clean temporary data in `finally`; do not run an unbounded benchmark or overwrite production caches.

## Shared implementation contract

All cloth-simulation tools should follow the repository's production contract:

- Require explicit scene, cloth object, modifier, collider, collection, vertex group, frame range, cache path, and coordinate-space inputs where ambiguity matters. Never rely on current selection, active object, mode, or frame.
- Validate complete batches before the first mutation. Reject missing/wrong types, non-finite settings, invalid vertex indices, unusable topology, bad ranges, name collisions, linked read-only data, and unsupported Blender capabilities clearly.
- Run all `bpy`, dependency-graph, simulation, cache, and operator work on Blender's main thread. Prefer RNA/data APIs; use operators only when Blender provides no safe data equivalent and supply a valid `context.temp_override`.
- Preserve active object, selection, mode, current frame, scene/view layer, and editor context with `try`/`finally`. Sampling and cache operations must leave the user's timeline and context coherent even on failure.
- Treat caches as derived but valuable production data. Mesh topology, transforms, modifier order, animation, vertex weights, material/solver/collision settings, colliders, fields, and frame ranges can invalidate them; never free, replace, or overwrite a bake implicitly.
- Default to non-destructive source, simulation-proxy, and render meshes with live modifiers. Do not apply scale, triangulate, remesh, bind, apply modifiers, clear groups, replace materials, or delete helpers unless the request explicitly authorizes that boundary.
- Tag cloth modifiers, proxies, colliders, controls, variants, and caches with stable simulation UUID, role, schema version, ownership, and source mapping. Use unique cache identities per object and variant.
- Extend rollback beyond newly created datablocks. In-place modifier/settings/group changes, topology edits, parenting, bind state, animation, and external cache files require snapshots or journals; filesystem cache deletion is not recoverable through Blender Undo.
- Return JSON-serializable results with changed objects/modifiers/settings/groups, cache state/path, invalidated dependencies, evaluated evidence, retained live components, warnings, and next useful validation or bake action.
- Bound object/vertex/collider counts, frame samples, quality settings, expected constraints/contact pairs, cache bytes, bake duration, and exports. Long sampling, baking, binding, profiling, and export operations need progress and cancellation.
- Add pure tests for schemas, ranges, weight roles, modifier ordering, resource estimates, cache state transitions, and path policies, plus Blender 5.1 runtime tests for modifier initialization, sewing, pressure, self-collision, moving colliders, attachments, character stacks, cache lifecycle, proxy binding, and export.

## Established cloth-simulation practices reflected in the plan

- Model at plausible real-world scale, apply or deliberately account for object scale before setup, and keep simulation edge lengths reasonably uniform. Cloth thickness, mass, stiffness, collision distance, and gravity are scale-sensitive.
- Use the lowest topology that preserves the required folds, then drive a separate render surface or add subdivision after Cloth. Dense simulation meshes and high-resolution colliders multiply solve cost.
- Place character deformation, hooks, and other intended rest-motion modifiers before Cloth; place corrective smoothing, subdivision, and solidify after it unless a tested shot requires another order.
- Establish normals, manifoldness requirements, pin groups, seam edges, collider proxies, and the rest frame before baking. Pressure requires a closed consistently oriented surface; sewing requires intentional loose-edge topology.
- Keep collision thickness proportional to mesh resolution. Resolve initial intersections first, increase solver/collision quality for fast motion, and use self-collision only where the shot needs it.
- Use gradual pin-weight transitions and stable attachment controls rather than a hard one-ring boundary when art direction allows; abrupt transitions commonly produce visible stress and jitter.
- Tune material response in stages: stretch/compression/shear, then bending, damping, collision, and finally secondary features such as pressure or internal springs. Changing many systems simultaneously obscures the cause of instability.
- Run short low-quality tests at representative high-motion and contact frames before a final bake. Review silhouette, penetration, popping, volume, seam closure, and temporal stability rather than trusting one still frame.
- Use unique, versioned caches for preview/final variants and lock topology, transforms, animation, modifier order, settings, colliders, and frame range before the approved bake.
- Treat presets as starting points, not calibrated fabric measurements. Document scale, mesh density, solver settings, vertex-weight maps, Blender version, and cache identity for reproducibility.

## What should not become separate tools

- Do not expose arbitrary cloth Python, generic unrestricted RNA setters, or a generic physics modifier command. Cloth, collision, and cache properties have different owners, validity rules, and invalidation effects.
- Do not create one tool per stiffness, damping, collision property, vertex-group role, field type, cache flag, or material preset. Use typed patches and discriminated roles.
- Do not split bake, free, status, and path configuration into scene-wide commands. One object-scoped cache lifecycle tool can enforce ownership, confirmation, and operator context consistently.
- Do not duplicate general mesh creation, transforms, animation, collection, material, or viewport tools where existing MCP primitives are sufficient.
- Do not combine cloth setup, proxy binding, final baking, render finishing, and export into one opaque operation. Their failure modes, costs, and destructive boundaries differ.
- Do not call `bpy.ops.ptcache.bake_all` or `free_bake_all`; scope cache operations to the exact Cloth modifier and point cache.
- Do not treat Cloth, Soft Body, Dynamic Paint, Geometry Nodes simulation, rigid bodies, and Mantaflow as interchangeable. They have distinct data models, solvers, caches, and delivery contracts.
- Do not promise automatic garment design, physically calibrated fabric, tearing, two-way rigid-body coupling, or artistic-quality folds from structural checks alone. Blender's native Cloth capabilities and the supplied topology define the reliable scope.

## Comparable MCP findings

Research snapshot: 2026-08-29. Repository capabilities can change after this date.

### `blender-ai-mcp`

`ahujasid/blender-mcp` provides broad scene inspection, arbitrary Blender code, rendering, and asset integrations, but its reviewed public server has no typed cloth, collider, sewing, pressure, or point-cache commands. A model can construct them through `execute_blender_code`, but that gives no stable ownership schema, cache invalidation contract, operator context, rollback, resource bounds, or structured validation. Its useful precedent is inspect-and-visually-verify; production cloth should not depend on arbitrary code execution.

### `blender-mcp-bridge`

`seehiong/blender-mcp-bridge` exposes extensive modeling, animation, materials, scene, and repeatable-workflow tools. Its published modifier command supports a curated set of modeling modifiers but does not expose Cloth, cloth settings, collision settings, sewing, pressure, or point-cache lifecycle. Its global parameter and replay concepts are useful for preview/final variants, but simulation cache identity and evaluated state also need to be first-class.

### `blender-mcp-pro`

`youichi-uda/blender-mcp-pro` provides a generic modifier creator and arbitrary modifier-parameter setter alongside broad scene and animation tools, but its reviewed handler tree has no cloth-physics module. Generic modifier access is insufficient because Cloth settings live on nested `ClothSettings`/`ClothCollisionSettings`, colliders use `CollisionSettings`, initialization can depend on evaluation, and baking uses an object-specific `PointCache` plus context-sensitive operators. The proposed tools therefore add a distinct, validated lifecycle rather than duplicating general modifier management.

### Current repository

This repository already provides strong prerequisites: focused mesh inspection, explicit vertex-group operations, evaluated modifier summaries, state restoration helpers, main-thread command dispatch, transaction tracking, and screenshot verification. Its cloth gap is complete. Implementation should live in `src/blender_mcp/server/tools/cloth.py` and `src/blender_mcp/bundled/addon/handlers/cloth.py`; shared cache, animation, vertex-group, matrix, and transaction helpers should be factored by responsibility and reused rather than copied.

Across the comparable MCPs, the highest-value gap is not raw access to a Cloth modifier. It is a reliable production lifecycle: scale/topology preflight, typed material and collision controls, deliberate weight maps and attachments, scoped point-cache operations, non-destructive proxy/render separation, evaluated validation, and explicit delivery.

## Sources

### Official Blender 5.1 documentation

- [ClothModifier API](https://docs.blender.org/api/5.1/bpy.types.ClothModifier.html)
- [ClothSettings API](https://docs.blender.org/api/5.1/bpy.types.ClothSettings.html)
- [ClothCollisionSettings API](https://docs.blender.org/api/5.1/bpy.types.ClothCollisionSettings.html)
- [CollisionSettings API](https://docs.blender.org/api/5.1/bpy.types.CollisionSettings.html)
- [PointCache API](https://docs.blender.org/api/5.1/bpy.types.PointCache.html)
- [EffectorWeights API](https://docs.blender.org/api/5.1/bpy.types.EffectorWeights.html)
- [FieldSettings API](https://docs.blender.org/api/5.1/bpy.types.FieldSettings.html)
- [VertexGroup API](https://docs.blender.org/api/5.1/bpy.types.VertexGroup.html)
- [Mesh API](https://docs.blender.org/api/5.1/bpy.types.Mesh.html)
- [Object operators](https://docs.blender.org/api/5.1/bpy.ops.object.html)
- [Point-cache operators](https://docs.blender.org/api/5.1/bpy.ops.ptcache.html)
- [Cloth manual](https://docs.blender.org/manual/en/5.1/physics/cloth/index.html)
- [Cloth physical properties](https://docs.blender.org/manual/en/5.1/physics/cloth/settings/physical_properties.html)
- [Cloth cache](https://docs.blender.org/manual/en/5.1/physics/cloth/settings/cache.html)
- [Cloth shape, pinning, sewing, and rest shape](https://docs.blender.org/manual/en/5.1/physics/cloth/settings/shape.html)
- [Cloth object and self-collisions](https://docs.blender.org/manual/en/5.1/physics/cloth/settings/collisions.html)
- [Cloth property weights](https://docs.blender.org/manual/en/5.1/physics/cloth/settings/property_weights.html)
- [Cloth field weights](https://docs.blender.org/manual/en/5.1/physics/cloth/settings/field_weights.html)
- [Cloth workflow examples](https://docs.blender.org/manual/en/5.1/physics/cloth/examples.html)
- [Physics collision objects](https://docs.blender.org/manual/en/5.1/physics/collision.html)
- [Cloth modifier](https://docs.blender.org/manual/en/5.1/modeling/modifiers/physics/cloth.html)
- [Collision modifier](https://docs.blender.org/manual/en/5.1/modeling/modifiers/physics/collision.html)

### Comparable projects and repository guidance

- [`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) (`blender-ai-mcp`)
- [`seehiong/blender-mcp-bridge`](https://github.com/seehiong/blender-mcp-bridge)
- [`youichi-uda/blender-mcp-pro`](https://github.com/youichi-uda/blender-mcp-pro)
- Repository `AGENTS.md` / `CLAUDE.md` production, safety, transaction, and Blender 5.1 requirements
