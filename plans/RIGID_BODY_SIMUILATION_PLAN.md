## Recommendation

Implement the P0 tools (1–12) first. Together they provide the reliable core of a production rigid-body workflow: scene and object inspection, world creation, active/passive body configuration, mass and collision filtering, proxy generation, constraints, and preflight validation. Add P1 for animation, destruction, mechanical assemblies, forces, sampling, caching, and keyframe delivery. Add P2 only after the core is live-tested in Blender 5.1, because proxy handoff, ragdolls, export, and performance analysis involve larger dependency graphs and more expensive evaluation.

The current MCP has no dedicated rigid-body surface. It already has useful prerequisites—object and mesh inspection, transformations, primitives, modifiers, booleans, collections, and transactional command dispatch—but rigid-body state is currently reachable only through arbitrary Python. The proposed commands should expose Blender’s native Bullet-backed rigid-body system through validated, inspectable operations and keep simulation sources editable until an explicit bake or export boundary.

## P0 — Core rigid-body setup

### 1. `get_rigid_body_scene_info`

**Description:** Inspect a scene’s rigid-body world, members, constraints, gravity, and cache without changing simulation state.

**Implementation details:** Resolve an explicit scene and return whether `Scene.rigidbody_world` exists; `Scene.use_gravity` and `Scene.gravity`; `RigidBodyWorld.enabled`, `time_scale`, `substeps_per_frame`, `solver_iterations`, and `use_split_impulse`; body and constraint collection names; member counts by active/passive, collision shape, and enabled state; effector weights; and `PointCache` frame range, disk/external flags, baked/baking/outdated state, and status text. Paginate member summaries and flag rigid-body objects outside the world collection. This tool is read-only and must not change the current frame or warm the cache.

### 2. `get_rigid_body_object_info`

**Description:** Inspect the complete rigid-body configuration and transform state of one object or a bounded explicit list.

**Implementation details:** Return object/data type, local and world transforms, dimensions, scale, parent, animation, collection membership, and every relevant `RigidBodyObject` field: `type`, `enabled`, `kinematic`, `mass`, collision shape/source/margin, deformation, friction, restitution, damping, deactivation thresholds, start-deactivated state, and the 20 collision-collection flags. Include evaluated bounds and approximate mesh complexity. Report whether settings are absent, whether mesh data is shared, and whether scale or modifiers make the collision representation ambiguous. Do not evaluate other frames unless explicitly requested.

### 3. `get_rigid_body_constraint_info`

**Description:** Inspect rigid-body constraint objects, connected bodies, limits, springs, motors, breakability, and solver overrides.

**Implementation details:** Read `Object.rigid_body_constraint` and return constraint-object transforms, `type`, `object1`, `object2`, `enabled`, `disable_collisions`, breaking settings, linear/angular limits and enable flags, linear/angular springs with stiffness and damping, motors with target velocity and maximum impulse, and solver-iteration overrides. Include world-constraint collection membership and identify missing endpoints, same-object endpoints, and constraints that reference non-rigid objects. Paginate bulk results.

### 4. `configure_rigid_body_world`

**Description:** Create or update a scene’s single rigid-body world with explicit solver, collection, gravity, and frame-range settings.

**Implementation details:** If no world exists, call `bpy.ops.rigidbody.world_add()` under a controlled scene/view-layer context and require `{'FINISHED'}`; `Scene.rigidbody_world` is not directly constructible. Configure `RigidBodyWorld.enabled`, `time_scale`, `substeps_per_frame`, `solver_iterations`, `use_split_impulse`, `collection`, `constraints`, and `PointCache.frame_start`, `frame_end`, and `frame_step`. Configure `Scene.use_gravity`/`gravity` separately and patch `EffectorWeights` only for supplied fields. Reuse or create deliberately named collections and never replace populated world collections silently. Refuse settings changes while a protected bake exists unless the caller first authorizes cache deletion.

### 5. `add_rigid_bodies`

**Description:** Add active or passive rigid-body settings to a fully validated list of objects and initialize production-safe defaults.

**Implementation details:** Validate every object before changing the first, including supported object/data type, unique names, finite transforms, and editability. Because `Object.rigid_body` is a read-only pointer created through an operator, establish each object as active in Object Mode and call `bpy.ops.rigidbody.object_add()` with a valid override, checking for `FINISHED`. Then assign typed `RigidBodyObject` properties through RNA. Support a source-settings object or explicit settings, place participants in the chosen world collection, preserve original scene collections, and restore selection/mode. Default to `CONVEX_HULL` for active mesh bodies and conservative passive shapes; do not silently use expensive concave `MESH` collision.

### 6. `configure_rigid_bodies`

**Description:** Patch physical and collision properties on existing rigid bodies without recreating them.

**Implementation details:** Support explicit fields for active/passive type, enabled/kinematic state, collision shape, `mesh_source`, deforming mode, margin, friction, restitution, linear/angular damping, deactivation, and start-deactivated behavior. Validate shape/type combinations and Blender 5.1 RNA enum values at runtime. Apply a complete batch atomically and return old/new settings per object. Changing physics settings invalidates cached results; refuse mutation of a baked world without explicit cache-delete authorization and mark unprotected caches outdated in the result.

### 7. `set_rigid_body_mass`

**Description:** Assign mass directly or derive it consistently from density and evaluated object volume.

**Implementation details:** Accept either positive mass or positive density, never both. For density mode, calculate closed-mesh volume from evaluated geometry using BMesh `calc_volume()` or an equivalently verified mesh-volume method, include the world-scale determinant, and set `RigidBodyObject.mass = density * volume`. Warn or fail on open/non-manifold meshes, near-zero volume, negative scale ambiguity, and unsupported non-mesh objects. Support batch normalization to a target total mass. Do not rely on Blender UI material presets unless the exact `bpy.ops.rigidbody.mass_calculate` material mapping is intentionally exposed and runtime-verified.

### 8. `set_rigid_body_collision_layers`

**Description:** Configure which rigid bodies can collide using explicit layer masks or stable named profiles.

**Implementation details:** Map supplied 1-based layer numbers to the 20-element `RigidBodyObject.collision_collections` boolean array. Support exact replace, add, and remove policies, plus optional repository-defined profiles such as `environment`, `hero`, `debris`, `ragdoll`, and `self_collision_off`. Validate every profile expansion before mutation. Return the normalized layers for each body and report pairs that now share no collision layer. Do not confuse rigid-body collision layers with Blender Collections or view layers.

### 9. `create_rigid_body_collision_proxy`

**Description:** Create a lightweight collision object for complex render geometry while preserving the visible source object.

**Implementation details:** Support bounding box, sphere, capsule approximation, cylinder, convex hull, and explicit low-resolution source mesh. For convex hulls, copy evaluated vertices into a new mesh and use `bmesh.ops.convex_hull` or a verified mesh operator under controlled context. Link proxies to a dedicated collection, tag source/proxy roles with a stable rig ID, optionally hide proxies from rendering, and preserve the source world transform. For active simulation, place the rigid body on the proxy and drive the render object through parenting or `COPY_TRANSFORMS`; for passive collision, keep the render object independent. Return approximation error indicators such as bounds/volume difference. Never replace or delete source geometry.

### 10. `create_rigid_body_constraint`

**Description:** Create a named fixed, point, hinge, slider, piston, generic, generic-spring, or motor constraint between explicit bodies.

**Implementation details:** Create or reuse an Empty at an explicit world transform, place it in the rigid-body world’s constraint collection, make it active, and call `bpy.ops.rigidbody.constraint_add()` under a valid override. Set `RigidBodyConstraint.type`, `object1`, `object2`, `disable_collisions`, and an initial typed configuration. Constraint axes derive from the constraint object’s local transform, so accept a quaternion/matrix or a documented axis and build the world matrix deterministically. Validate distinct endpoints with rigid-body settings and tag the constraint object with rig ID and role.

### 11. `configure_rigid_body_constraint`

**Description:** Update a constraint’s limits, springs, motors, breaking behavior, collision policy, endpoints, or solver override.

**Implementation details:** Use a discriminated schema per constraint type rather than one unvalidated property bag. Set corresponding `use_limit_lin_*`, `limit_lin_*_lower/upper`, `use_limit_ang_*`, angular limits in radians, `use_spring_*`, stiffness/damping, `use_motor_lin`/`use_motor_ang`, target velocities, maximum impulses, `use_breaking`, `breaking_threshold`, and solver override fields. Validate lower ≤ upper, finite values, nonnegative stiffness/damping/impulses, compatible properties, and both endpoints before mutation. Return active degrees of freedom and ignored-property warnings rather than accepting meaningless settings.

### 12. `validate_rigid_body_setup`

**Description:** Run a non-mutating production preflight for world, body, constraint, collision, and cache problems.

**Implementation details:** Detect missing/disabled worlds, bodies or constraints outside their world collections, invalid cache ranges, stale or baked-cache conflicts, unapplied or extreme scale, non-finite transforms, zero/implausible mass, extreme mass ratios, active concave mesh shapes, excessive collision complexity, inappropriate margins, initial interpenetration, unsupported deforming bodies, missing constraint endpoints, invalid limits, constraint-frame misalignment, disconnected collision layers, fast-small-object tunneling risk, insufficient substeps/iterations, and animated objects lacking intentional kinematic control. Use evaluated bounds plus `mathutils.bvhtree.BVHTree.overlap` for bounded mesh-pair checks when necessary. Return severity, object/constraint, evidence, and remediation; do not claim physical correctness without simulation sampling.

## P1 — Animation, mechanisms, destruction, and caching

### 13. `remove_rigid_body_components`

**Description:** Explicitly remove rigid-body settings, constraint settings, helper objects, or the scene world with precise scope and confirmation.

**Implementation details:** Support distinct actions for body settings (`bpy.ops.rigidbody.object_remove`), constraint settings (`constraint_remove`), tagged MCP helper objects, and world removal (`world_remove`). Pre-resolve and report every target. Removing a world, a baked cache, or helper objects is destructive and requires explicit confirmation; removing body settings must not delete the mesh object. Never use broad current-selection removal. Return what was removed, what remained, and whether the action can be recovered through Blender Undo.

### 14. `animate_rigid_body_release`

**Description:** Hand an object from authored animation to rigid-body simulation, or back again, at explicit frames.

**Implementation details:** Keyframe `RigidBodyObject.kinematic` and the object transform around the transition using `keyframe_insert`. For a release with intended linear/angular motion, solve a bounded pre-roll transform from velocity and frame rate, key the preceding transform while kinematic, then key `kinematic=False` at release; disclose that Blender derives inherited motion through the animated transition rather than exposing a direct initial-velocity RNA property. Support pickup/re-capture by keying kinematic on and matching the evaluated transform first. Preserve existing actions through a new action or dedicated NLA track unless overwrite is explicit.

### 15. `create_compound_rigid_body`

**Description:** Build an efficient compound collision representation for a concave active object from explicit child proxies.

**Implementation details:** Create or designate a root rigid body with `collision_shape='COMPOUND'`, parent validated convex/simple proxy children while preserving world transforms, and configure the child collision shapes according to the Blender 5.1 compound-body contract verified at runtime. Keep the render mesh separate or parented as a non-collider. Tag all parts, store source mappings, and compute total mass or distribute mass intentionally. Reject cycles, nested ownership conflicts, or unverified child membership; validate the assembled proxy against source bounds.

### 16. `create_rigid_body_constraint_network`

**Description:** Build many constraints deterministically for chains, bridges, machinery, suspended props, or fracture bonds.

**Implementation details:** Accept explicit body pairs or generate pairs using bounded nearest-neighbor, radius, chain-order, or parent-hierarchy rules. Precompute the complete edge list, reject duplicates/self-links, cap degree and total constraints, then call the same internal builder used by `create_rigid_body_constraint`. Support per-edge or shared typed constraint settings, midpoint/aligned frames, endpoint pinning to passive anchors, and stable naming. Roll back the whole network on failure and return nodes, edges, connected components, and constraint roles.

### 17. `prepare_fracture_rigid_bodies`

**Description:** Turn an existing, explicitly supplied collection of fracture pieces into a controlled destruction simulation.

**Implementation details:** This tool does not fracture geometry. Validate pre-fractured closed mesh pieces, assign active bodies, calculate mass from density/volume, choose convex-hull or simple proxies, configure deactivation and margins, and optionally build breakable fixed/generic constraints between nearby pieces. Preserve shard naming/materials and isolate physics helpers in dedicated collections. Return total mass, shard size distribution, initial-overlap findings, and constraint count. Blender core has no stable general-purpose fracture operator; an optional Cell Fracture integration should be a separately capability-checked feature, not a hidden dependency.

### 18. `create_rigid_body_chain`

**Description:** Build a stable chain, pendulum, linkage, hinge assembly, slider mechanism, or motorized mechanical rig from ordered bodies.

**Implementation details:** Validate an explicit ordered body list, create adjacent constraints with local axes derived from body transforms, and optionally add passive endpoint anchors. Support hinge, point, fixed, slider, piston, generic-spring, and motor templates through the core constraint APIs. Expose collision-between-neighbors, limits, damping, stiffness, motor target/impulse, and solver overrides. Estimate link length/mass ratios and recommend world substeps/iterations without silently changing them.

### 19. `setup_animated_passive_collider`

**Description:** Configure an animated environment object—doors, elevators, vehicles, machinery, characters, or deforming surfaces—as a passive collider.

**Implementation details:** Add or configure `type='PASSIVE'`, set `kinematic=True` for animated motion, choose `mesh_source` and `use_deform` based on whether topology/deformation changes, and select a collision shape appropriate to the evaluated geometry. Preserve existing animation and modifiers. For deforming mesh collision, warn about cost and require bounded complexity; prefer primitive/convex proxies for rigid transforms. Validate motion over sampled frames for teleporting, scale animation, and collider discontinuities.

### 20. `configure_rigid_body_force_fields`

**Description:** Create or configure force-field effectors and control how strongly the rigid-body world responds to them.

**Implementation details:** Create a named Empty/object and configure its `FieldSettings` for supported types such as FORCE, WIND, VORTEX, TURBULENCE, DRAG, or HARMONIC after Blender 5.1 runtime validation. Expose strength, flow, noise, seed, shape, falloff type/power, min/max distance, and location/rotation influence. Configure `RigidBodyWorld.effector_weights`, including an optional effector collection, without modifying unrelated physics systems. Return created effectors, world weights, spatial bounds, and animation dependencies. Keep gravity in `configure_rigid_body_world`.

### 21. `sample_rigid_body_simulation`

**Description:** Evaluate selected frames and return rigid-body transforms and stability diagnostics without converting the simulation to animation.

**Implementation details:** Require a bounded ordered frame list or start/end/step. Advance sequentially with `scene.frame_set`, read evaluated `matrix_world` from the dependency graph, and derive approximate linear/angular velocity from consecutive samples when requested. Return transforms, bounds, sleeping/stationary heuristics, constraint distances, penetrations detectable from bounded proxy tests, non-finite results, and escape-from-bounds events. Restore the original frame in `finally`. Sampling may populate Blender’s temporary point cache, so report that side effect and never describe it as a protected bake.

### 22. `manage_rigid_body_cache`

**Description:** Inspect, calculate-to-frame, bake, bake-from-cache, or explicitly free the rigid-body world cache.

**Implementation details:** Operate on `RigidBodyWorld.point_cache` with explicit frame range and action. Use `bpy.ops.ptcache.bake`, `bake_from_cache`, or `free_bake` under the exact scene/point-cache context, in Object Mode, and verify `PointCache.is_baked`, `is_baking`, `is_outdated`, and `info` afterward. Avoid scene-wide `bake_all`/`free_bake_all` because they affect unrelated physics. Baking/freeing is expensive or destructive and requires explicit authorization. External cache mode requires a user-provided path, saved-file awareness, path validation, size/time limits, progress, cancellation, and failure-safe cleanup.

### 23. `bake_rigid_bodies_to_keyframes`

**Description:** Convert selected simulated bodies to ordinary transform animation over an explicit frame range while preserving the simulation source by default.

**Implementation details:** Prefer duplicating target objects or recording into a new action/NLA track, then sample evaluated world matrices sequentially and convert them correctly through parent space. Key location and quaternion rotation at the requested step; optionally key scale only when the evaluated result changes it. Blender’s `bpy.ops.rigidbody.bake_to_keyframes` may be offered as a verified backend under a controlled selection/mode context, but it must not overwrite actions without confirmation. Support optional key reduction with error tolerances after sampling. Return action names, channels, frames, and retained source bodies.

## P2 — Character, proxy, interchange, and scale

### 24. `create_rigid_body_debris_field`

**Description:** Create a bounded, deterministic set of rigid-body debris objects from reusable source meshes for VFX impacts and environmental motion.

**Implementation details:** Duplicate objects with linked mesh data by default, distribute them inside an explicit box/sphere/collection-bounds region using a supplied seed, and assign transforms from validated ranges. Add active rigid bodies, calculate masses from density or source weights, set collision layers/deactivation, and optionally start pieces deactivated for impact activation. Because independent Bullet bodies must be real objects rather than Geometry Nodes instances, enforce a strict object-count limit and organize them in a dedicated collection. Return seed, source mapping, total mass, count, and overlap findings.

### 25. `create_rigid_body_proxy_rig`

**Description:** Build a low-resolution simulation rig that drives one or more high-resolution render assets.

**Implementation details:** Accept explicit render-to-proxy mappings or generate proxy shapes through `create_rigid_body_collision_proxy`. Put physics only on proxies, drive render objects using parent or `COPY_TRANSFORMS` relationships with correct inverse matrices, and preserve render modifiers/materials. Support compound render assets and a shared root while rejecting dependency cycles. Separate proxy, controls, and render collections; tag roles and visibility. Validate evaluated transform agreement at the start frame and after a short bounded simulation sample.

### 26. `create_ragdoll_rig`

**Description:** Build a rigid-body proxy and constraint rig from an explicit armature bone mapping for character falls, impacts, and secondary simulation.

**Implementation details:** Require a bone-to-shape specification or validated generation rules. Create capsule, box, or convex proxy objects aligned to pose-bone world matrices, assign mass by volume/body-region weights, configure collision layers, and connect adjacent proxies with point, hinge, or generic-spring constraints using anatomical angular limits. Use a separate tagged collection and preserve the armature and animation. Provide kinematic activation timing through `animate_rigid_body_release`. Do not infer production joint limits silently; return generated defaults for review and validate self-collision and initial overlap.

### 27. `bake_ragdoll_to_armature`

**Description:** Transfer evaluated ragdoll motion back onto pose bones in a new animation layer for editing and export.

**Implementation details:** Sample proxy `matrix_world` values over a bounded frame range, solve each mapped pose bone’s matrix in armature/object and parent space, and key pose-bone location plus quaternion rotation. Write to a new `Action` or NLA track and preserve the original authored action. Support blend-in/out intervals and key reduction with explicit positional/angular tolerances. Restore frame, mode, pose state, and active action on failure. Verify a sample of baked bone/proxy world transforms and report maximum deviation.

### 28. `export_rigid_body_animation`

**Description:** Export baked rigid-body or ragdoll motion for DCC, game-engine, compositor, or archive handoff.

**Implementation details:** Require an explicit path, format, frame range, coordinate convention, unit scale, and overwrite confirmation. Offer a deterministic JSON baseline containing object IDs, hierarchy, per-frame world matrices, frame rate, collision metadata, and constraint metadata. Offer Alembic, USD, glTF, or FBX only after verifying Blender 5.1 exporter support and state whether the format carries transforms, meshes, constraints, or only baked animation. Bake to temporary duplicates when required and remove them in `finally`. Verify the output exists and preserve source simulation/cache.

### 29. `analyze_rigid_body_performance`

**Description:** Diagnose stability and performance bottlenecks using bounded simulation sampling and structural heuristics.

**Implementation details:** Measure controlled sequential evaluation over a small explicit frame range and separately report wall-clock timing and heuristic findings. Analyze active/passive counts, collision-shape complexity, triangle counts for mesh colliders, compound part counts, pair-filter opportunities, initial overlaps, size/speed ratio, mass ratios, constraints per island, solver overrides, substeps, iterations, deforming colliders, and cache state. Flag early tunneling, jitter, explosive separation, sleeping failures, and constraint drift from sampled transforms. Blender does not expose full Bullet contact/profiler data through stable RNA, so never invent per-contact or per-body solver timings.

## Shared implementation contract

All rigid-body tools should follow the repository’s production contract:

- Require explicit scene, object, collection, constraint, frame-range, and coordinate-space inputs. Never infer simulation members from the current selection.
- Inspect before mutation. Resolve the world, cache state, object types, transforms, scale, modifier output, collection membership, and existing animation before changing physics.
- Validate complete batches before the first mutation. Reject missing or linked read-only data, duplicates, unsupported types, invalid ranges/enums, non-finite transforms, unsafe object/frame counts, and inconsistent constraint endpoints.
- Run every `bpy` access and dependency-graph evaluation on Blender’s main thread. Prefer RNA for settings; use `bpy.ops.rigidbody` and `bpy.ops.ptcache` only where Blender requires operators and supply a valid override.
- Preserve active object, selection, mode, current frame, active action, and editor context with `try`/`finally`. Require `{'FINISHED'}` and turn cancellations into actionable errors.
- Treat cached simulation as derived production data. Any world, body, collision, transform, force, constraint, or frame-range edit can invalidate it. Refuse to mutate a protected bake unless deletion was explicitly authorized.
- Default to non-destructive proxies, live constraints, separate source/render collections, and new actions or duplicate delivery objects. Do not apply transforms, replace render meshes, delete helpers, or overwrite animation implicitly.
- Keep scale physically meaningful and spaces explicit. Collision margin, object dimensions, gravity, velocity, mass/density, and constraint frames must use documented scene units and local/world conventions.
- Tag generated proxies, constraints, anchors, debris, and ragdoll parts with stable simulation UUID, role, schema version, source mapping, and ownership metadata.
- Extend transaction rollback beyond newly created datablocks. In-place rigid-body/world/constraint settings, collection assignments, keyframes, point-cache state, and external cache files need explicit snapshots or rollback journals.
- Return JSON-serializable results containing changed bodies/constraints/world settings, created helpers, collision and mass assumptions, cache invalidation, evaluated verification, retained live state, warnings, and next safe action.
- Bound object counts, collision-pair checks, BVH tests, sampled frames, solver settings, cache size/time, debris counts, fracture constraints, and export payloads. Long sampling/bake/export work needs progress and cancellation.
- Add pure tests for validation, layer masks, mass/volume math, pair generation, constraint schemas, and transform conversion, plus Blender 5.1 runtime tests for every collision shape, world membership, constraints, kinematic release, cache lifecycle, proxy driving, and keyframe baking.

## Established rigid-body practices reflected in the plan

- Model and simulate at plausible real-world scale. Apply or account for object scale before trusting collision margins, mass, and solver behavior.
- Prefer primitive and convex-hull collision shapes. Use concave mesh collision mainly for passive environments; use compound/simple proxies for concave active assets.
- Keep collision proxies low-resolution, closed, and free of accidental self-intersection. Visual geometry and collision geometry should be separate when production complexity warrants it.
- Start with physically plausible mass and avoid extreme mass ratios within a connected constraint island. Derive mass from volume and density when consistency matters.
- Resolve initial intersections before increasing solver quality. Then raise substeps for fast/small bodies and solver iterations for stacks and constraint-heavy systems.
- Use collision layers to eliminate impossible interactions and improve both speed and determinism.
- Use deactivation for settled debris, but disable or tune it for hero objects that must remain responsive.
- Animate `kinematic` for handoff between authored and simulated motion; preserve source animation in a separate action or NLA layer.
- Simulate with low-resolution proxies, validate representative frames, bake the approved world cache, and only then bake transforms or export.
- Version world settings, body presets, random seeds, proxy mappings, and cache ranges. Identical scene inputs and frame stepping are essential for repeatable review.

## What should not become separate tools

- Do not expose arbitrary rigid-body Python or a generic unrestricted RNA property setter. Typed schemas are essential because many invalid combinations fail only during evaluation.
- Do not create one tool per collision shape, constraint type, limit axis, spring axis, motor channel, or force-field type. Use discriminated variants in the shared configuration tools.
- Do not split every world property, material response value, damping value, or collision layer into separate calls. Atomic validated patches prevent half-configured simulations.
- Do not duplicate existing primitive, transform, collection, modifier, or mesh-inspection tools inside the physics namespace.
- Do not combine cache baking, transform-key baking, ragdoll-to-armature transfer, and export. Each has a different data-loss and authorization boundary.
- Do not use `bpy.ops.ptcache.bake_all` or `free_bake_all`; those operators can affect unrelated cloth, particles, soft bodies, and other scene caches.
- Do not expose a generic fracture tool until a deterministic Blender 5.1 backend is available and tested. Core Blender has no general Cell Fracture operator; optional extensions must be capability-checked and explicit.
- Do not promise direct contact events, impulses, or exact Bullet velocities when Blender’s public RNA does not expose them. Derived sampling metrics must be labeled approximate.

## Comparable MCP findings

Research snapshot: 2026-08-29. Repository capabilities can change after this date.

### `blender-ai-mcp`

`ahujasid/blender-mcp` provides general scene inspection, code execution, asset integrations, and screenshots, but its reviewed public MCP server has no dedicated rigid-body commands. An agent can construct simulations through `execute_blender_code`, but that bypasses typed settings, cache protection, context restoration, validation, and stable result schemas. Its strongest applicable lesson is before/after scene inspection and visual verification; production physics should not depend on arbitrary scripts.

### `blender-mcp-bridge`

`seehiong/blender-mcp-bridge` exposes a broad task-level modeling set, generic modifier support, architecture/MEP builders, session recording/replay, parameter substitution, branching, undo/redo, and print validation. Its published tool reference reviewed for this plan contains no dedicated rigid-body world, body, constraint, or cache workflow. Session replay and global parameters are useful precedents for repeatable simulations, but rigid-body state also needs explicit cache invalidation and evaluated-frame verification.

### `blender-mcp-pro`

`youichi-uda/blender-mcp-pro` advertises broad object, modifier, animation, rigging, constraint, and node functionality, but its reviewed handler tree has no physics or rigid-body module. Generic object constraints are not Bullet rigid-body constraints, and general animation tools do not manage a `RigidBodyWorld` or `PointCache`. A focused rigid-body surface therefore adds distinct capability rather than renaming an existing tool family.

### Current repository

This repository’s structured mesh operations, evaluated modifier summaries, main-thread dispatch, state restoration, transaction wrapper, and explicit production rules are a strong base. The missing pieces are rigid-body inspection, safe operator contexts, cache-aware mutations, collision/proxy validation, constraint schemas, and frame-sampled verification. Implementation should live in `src/blender_mcp/server/tools/rigid_body.py` and `src/blender_mcp/bundled/addon/handlers/rigid_body.py`; read-only inspection commands should be added to `_READ_ONLY_COMMANDS`, while sampling must be treated as evaluation that can populate temporary cache state.

Across the comparable MCPs, the highest-value gap is not access to `bpy.ops.rigidbody`—arbitrary code already offers that—but reliable simulation lifecycle management. The MCP should make world scope, collision representation, constraint axes, cache state, validation, and delivery explicit and recoverable.

## Sources

### Official Blender 5.1 documentation

- [RigidBodyObject API](https://docs.blender.org/api/5.1/bpy.types.RigidBodyObject.html)
- [RigidBodyWorld API](https://docs.blender.org/api/5.1/bpy.types.RigidBodyWorld.html)
- [RigidBodyConstraint API](https://docs.blender.org/api/5.1/bpy.types.RigidBodyConstraint.html)
- [PointCache API](https://docs.blender.org/api/5.1/bpy.types.PointCache.html)
- [EffectorWeights API](https://docs.blender.org/api/5.1/bpy.types.EffectorWeights.html)
- [FieldSettings API](https://docs.blender.org/api/5.1/bpy.types.FieldSettings.html)
- [Scene API](https://docs.blender.org/api/5.1/bpy.types.Scene.html)
- [Object API and dependency-graph evaluation](https://docs.blender.org/api/5.1/bpy.types.Object.html)
- [Rigid-body operators](https://docs.blender.org/api/5.1/bpy.ops.rigidbody.html)
- [Point-cache operators](https://docs.blender.org/api/5.1/bpy.ops.ptcache.html)
- [BMesh API](https://docs.blender.org/api/5.1/bmesh.types.html)
- [BVHTree utilities](https://docs.blender.org/api/5.1/mathutils.bvhtree.html)
- [Rigid Body manual](https://docs.blender.org/manual/en/5.1/physics/rigid_body/index.html)
- [Rigid Body introduction](https://docs.blender.org/manual/en/5.1/physics/rigid_body/introduction.html)
- [Rigid Body settings](https://docs.blender.org/manual/en/5.1/physics/rigid_body/properties/settings.html)
- [Rigid Body collisions](https://docs.blender.org/manual/en/5.1/physics/rigid_body/properties/collisions.html)
- [Rigid Body dynamics](https://docs.blender.org/manual/en/5.1/physics/rigid_body/properties/dynamics.html)
- [Rigid Body World and cache](https://docs.blender.org/manual/en/5.1/physics/rigid_body/world.html)
- [Rigid Body Constraints](https://docs.blender.org/manual/en/5.1/physics/rigid_body/constraints/index.html)
- [Rigid Body tips and stability](https://docs.blender.org/manual/en/5.1/physics/rigid_body/tips.html)
- [Force fields](https://docs.blender.org/manual/en/5.1/physics/forces/force_fields/introduction.html)

### Comparable projects and repository guidance

- [`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) (`blender-ai-mcp`)
- [`seehiong/blender-mcp-bridge`](https://github.com/seehiong/blender-mcp-bridge)
- [`youichi-uda/blender-mcp-pro`](https://github.com/youichi-uda/blender-mcp-pro)
- Repository `AGENTS.md` / `CLAUDE.md` production, safety, transaction, and Blender 5.1 requirements

