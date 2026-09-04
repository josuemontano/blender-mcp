## Recommendation

Implement the P0 tools (1–12) first. Together they cover the safe foundation of a production liquid workflow: inspection, domain creation and fitting, solver configuration, flows, effectors, boundary/collection control, resource estimation, and validation. Add P1 for liquid meshing, viscosity, secondary particles, animation, guiding, forces, materials, cache management, and teardown. Add P2 only after live Blender 5.1 testing because proxy rigs, variant duplication, render finishing, sequence export, and performance analysis interact with large caches and evaluated geometry.

The current MCP has no dedicated Mantaflow tool surface. Existing object, transform, primitive, modifier, material, collection, inspection, and screenshot tools remain useful building blocks, but liquid state is currently reachable only through arbitrary Python. The new commands should expose Blender’s native liquid workflow through typed, cache-aware operations and keep source objects and cache stages recoverable.

## P0 — Core liquid setup

### 1. `get_liquid_simulation_info`

**Description:** Inspect all liquid domains and their associated flows, effectors, guides, collections, cache stages, and outputs without changing the scene.

**Implementation details:** Resolve an explicit scene or domain and return each `FluidModifier` with `fluid_type`, domain type, object/world transforms, bounds, resolution, time settings, simulation method, boundary state, flow/effector/force collections, liquid mesh and secondary-particle flags, diffusion settings, guide settings, cache directory/type/formats/frame range, pause frames, and all `has_cache_baked_*`/`is_cache_baking_*` flags. Identify flows and effectors through modifier settings and collection membership, and paginate them. Report missing dependencies and modifiers outside the domain’s collections. This tool is read-only and must not advance frames or initialize a bake.

### 2. `get_fluid_object_info`

**Description:** Inspect the complete fluid role and relevant geometry/animation state of one domain, flow, or effector object.

**Implementation details:** Return object/data type, local and world transforms, dimensions, scale, modifier order, animation, evaluated bounds/counts, and the applicable `FluidDomainSettings`, `FluidFlowSettings`, or `FluidEffectorSettings` fields. For flows include `flow_type`, `flow_behavior`, `flow_source`, plane/surface settings, subframes, initial velocity, velocity components, particle source, vertex group, and inflow state. For effectors include collision/guide type, enabled state, plane initialization, surface distance, subframes, guide mode, and velocity factor. Represent referenced IDs by type/name and bound large data.

### 3. `create_liquid_domain`

**Description:** Create a new liquid domain on an explicit mesh object or create a new box domain with production-safe defaults.

**Implementation details:** For a new box, create a collision-safe mesh/object with dimensions baked into mesh coordinates so object scale remains `(1, 1, 1)`, then link it to an explicit collection. Add `obj.modifiers.new(name=..., type='FLUID')`, set `FluidModifier.fluid_type='DOMAIN'`, update the view layer if required for settings initialization, and set `domain_settings.domain_type='LIQUID'`. Configure an explicit cache directory/type/frame range and base resolution, simulation method, time scale, and adaptive-timestep values. Create or reuse named flow/effector collections and tag domain ownership. Roll back the object, mesh, modifier, collections, and cache path created by the request if setup fails.

### 4. `fit_liquid_domain`

**Description:** Compute or update a tight domain around explicit sources, colliders, and expected motion with controlled padding.

**Implementation details:** Evaluate world-space bounds at the start frame or across a bounded sampled frame range, union the requested objects, add per-axis padding, and either create a new unit-scale box domain or update an unbaked domain. Account for inflow travel, splash height, gravity direction, collider motion, and open boundaries supplied by the caller. Return world bounds, dimensions, cell-size estimate, and limiting axis. Never resize a baked domain; refuse or create a new variant. Do not apply transforms to an existing user object without explicit authorization.

### 5. `configure_liquid_solver`

**Description:** Patch the core liquid solver, time stepping, particle sampling, and domain-resolution settings.

**Implementation details:** Configure verified Blender 5.1 properties such as `resolution_max`, `time_scale`, `timesteps_min`, `timesteps_max`, `use_adaptive_timesteps`, `cfl_condition`, `simulation_method`, `flip_ratio`, `particle_randomness`, `particle_number`, `particle_min`, `particle_max`, `particle_radius`, `particle_band_width`, `use_fractions`, `fractions_threshold`, and `fractions_distance`. Use a typed schema with documented units/ranges and runtime RNA validation. Patch only supplied values and return old/new settings plus estimated grid size. Refuse changes while relevant cache stages are baked unless their deletion is explicitly authorized.

### 6. `add_liquid_flow`

**Description:** Add a liquid flow modifier to an explicit mesh or particle source and register it with a domain.

**Implementation details:** Validate the source and target domain, add a `FLUID` modifier, set `fluid_type='FLOW'`, initialize settings, then set `flow_type='LIQUID'` and the requested behavior (`GEOMETRY`, `INFLOW`, or `OUTFLOW`). Support mesh or particle-system sources only when compatible with Blender 5.1. Add the source to the domain’s `fluid_group` collection without removing its existing scene links. Configure initial velocity, surface distance, plane initialization, source particles, and subframes through the same internal validation used by `configure_liquid_flow`. Return created modifier, collection membership, and cache invalidation.

### 7. `configure_liquid_flow`

**Description:** Update a liquid source’s behavior, emission timing, source geometry, subframes, and velocity contribution.

**Implementation details:** Patch `FluidFlowSettings.flow_behavior`, `flow_source`, `use_inflow`, `use_plane_init`, `surface_distance`, `subframes`, `use_initial_velocity`, `velocity_coord`, `velocity_factor`, `velocity_normal`, `velocity_random`, particle-system reference, `use_particle_size`, `particle_size`, density vertex group, and other liquid-applicable fields after runtime validation. Reject smoke/fire-only parameters. Make object/local/source velocity semantics explicit and validate referenced vertex groups and particle systems. For batch edits, preflight all objects before mutation.

### 8. `add_liquid_effector`

**Description:** Add an obstacle or guide modifier to an explicit object and register it with a liquid domain.

**Implementation details:** Add a `FLUID` modifier and set `fluid_type='EFFECTOR'`, then configure `FluidEffectorSettings.effector_type` as `COLLISION` or `GUIDE`. Set `use_effector`, `use_plane_init`, `surface_distance`, `subframes`, guide mode, and velocity factor as applicable. Link the object to the domain’s effector collection while preserving its existing collections. Inspect evaluated topology and animation; recommend proxy geometry for high-resolution, deforming, thin, or fast-moving colliders. State clearly that Mantaflow collision is one-way and does not apply fluid forces back to rigid bodies.

### 9. `configure_liquid_effector`

**Description:** Patch collision or guide behavior on existing liquid effectors without rebuilding them.

**Implementation details:** Resolve the exact `FLUID` effector modifier, validate its domain association, and update only supplied `FluidEffectorSettings` fields: enabled state, effector type, plane initialization, surface distance, subframes, guide mode, and velocity factor. Detect unsupported topology changes, scale animation, and modifier ordering. Return old/new values and cache stages invalidated by the change. Do not add rigid-body physics or alter the object’s animation.

### 10. `configure_liquid_scope_and_boundaries`

**Description:** Control which flows, effectors, and forces affect a domain and which domain faces are open or colliding.

**Implementation details:** Set `FluidDomainSettings.fluid_group`, `effector_group`, and `force_collection` to explicit collections, creating named collections only when requested. Configure `use_collision_border_front`, `back`, `left`, `right`, `top`, and `bottom` through a documented domain-local face mapping; include the domain world matrix in the response so orientation is unambiguous. Preserve objects’ other collection links. Validate that intended flows/effectors are members and report objects that fall outside the domain bounds.

### 11. `estimate_liquid_resources`

**Description:** Estimate resolution, grid dimensions, output scale, and relative cache cost before an expensive simulation.

**Implementation details:** Use the domain’s world dimensions and `resolution_max` along its longest axis to estimate base cell size and per-axis cell counts; include `mesh_scale`, particle settings, frame count, enabled mesh/secondary stages, and cache format in a conservative relative cost model. If initialized runtime values such as `cell_size` or `domain_resolution` are available, report them separately from estimates. Recommend preview/final ranges without modifying settings. Do not promise exact memory, disk, or bake time because occupancy, motion, compression, hardware, and solver behavior determine the result.

### 12. `validate_liquid_setup`

**Description:** Run a non-mutating production preflight over the domain, sources, effectors, cache, and render outputs.

**Implementation details:** Detect multiple overlapping liquid domains, missing or wrong modifier roles, unapplied/nonuniform/negative scale, zero-size or thin geometry, non-manifold flow/collider meshes, flows outside the domain, domain clipping, inconsistent frame ranges, invalid cache directories, shared cache paths, incompatible cache types/stages, stale or partial bakes, low resolution for feature size, insufficient subframes for fast motion, excessive velocity versus CFL/time steps, unsupported animated topology, outflow placement errors, closed boundaries that should vent, meshing disabled for render delivery, speed vectors enabled too late, and secondary particles without prerequisite data. Return severity, object/property/frame, evidence, and remediation; never claim fluid quality without sampling or a rendered review.

## P1 — Liquid appearance, motion, and cache lifecycle

### 13. `configure_liquid_mesh`

**Description:** Configure generation of the renderable liquid surface from simulation particles.

**Implementation details:** Set `use_mesh`, `mesh_scale`, `mesh_particle_radius`, `mesh_smoothen_pos`, `mesh_smoothen_neg`, `mesh_concave_upper`, `mesh_concave_lower`, `mesh_generator`, `use_speed_vectors`, and mesh cache format fields supported by Blender 5.1. Validate positive scale/radius, bounded up-resolution, and cache-type prerequisites. Enable speed vectors before baking when motion blur/export needs them. Report estimated output resolution and whether data must be rebaked. Do not apply smoothing/subdivision modifiers to the domain in this tool; that belongs to render finishing.

### 14. `configure_liquid_secondary_particles`

**Description:** Configure spray, foam, bubble, and tracer generation for detailed VFX liquid shots.

**Implementation details:** Set `use_spray_particles`, `use_foam_particles`, `use_bubble_particles`, `use_tracer_particles`, `sndparticle_combined_export`, boundary behavior, life ranges, potential thresholds for wave crest/trapped air/kinetic energy, sampling, update radius, bubble buoyancy/drag, and particle scale using a typed Blender 5.1 schema. Enforce sensible ranges and cache prerequisites, and explain combined versus separate export behavior. Secondary particles are expensive and should be disabled in preview presets by default.

### 15. `configure_liquid_diffusion`

**Description:** Configure viscosity, high-viscosity solving, and surface tension for water, oil, honey, molten material, and stylized liquids.

**Implementation details:** Patch `use_diffusion`, `viscosity_base`, `viscosity_exponent`, `use_viscosity`, `viscosity_value`, and `surface_tension`. Accept direct Blender values or a named preset whose exact numeric expansion is returned and versioned. If accepting dynamic viscosity and density, convert explicitly to kinematic viscosity in square meters per second before mapping to Blender’s base/exponent representation. Validate scale and solver suitability, and warn that extremely high viscosity does not create rigid-body behavior and may destabilize the solver.

### 16. `animate_liquid_flow`

**Description:** Schedule inflow, outflow, or moving-source emission with explicit keyframes and subframe support.

**Implementation details:** Key `FluidFlowSettings.use_inflow` and other keyable flow parameters at explicit frames, using constant interpolation for hard on/off events unless requested otherwise. Preserve existing animation by writing a dedicated action/NLA track where Blender’s ownership model permits; otherwise require an overwrite/merge policy. For moving emitters, validate transform animation, set sufficient `subframes`, and configure initial velocity consistently. Return keyed data paths, frames, interpolation, and cache invalidation. A one-shot `GEOMETRY` source should not be presented as a continuous inflow.

### 17. `create_liquid_guide`

**Description:** Build or configure a velocity guide for art-directed liquid motion.

**Implementation details:** Add a GUIDE effector with `FluidEffectorSettings.guide_mode` and velocity factor, or configure a guide domain/source through `FluidDomainSettings.use_guide`, `guide_source`, `guide_parent`, `guide_alpha`, `guide_beta`, and `guide_vel_factor` after runtime validation. Support explicit guide objects/collections and frame ranges. Keep guide cache separate from data/mesh/particle stages and use `bpy.ops.fluid.bake_guides` only through cache management. Report required bake order and reject circular domain dependencies.

### 18. `configure_liquid_force_fields`

**Description:** Create or assign force-field effectors and control their influence on one liquid domain.

**Implementation details:** Reuse the repository’s future/shared force-field helper to create named objects with verified `FieldSettings`, including FORCE, WIND, VORTEX, TURBULENCE, DRAG, and other liquid-relevant types. Configure domain `effector_weights` and `force_collection` only for supplied fields. Expose strength, falloff, noise, seed, and transform with explicit units/space. Keep scene gravity and domain gravity reporting distinct. Warn that force effects depend on domain resolution/time steps and are not bidirectional rigid-body coupling.

### 19. `create_liquid_material`

**Description:** Create or assign a physically plausible transparent liquid material to the generated liquid mesh.

**Implementation details:** Build a named node-based material with Principled BSDF transmission, IOR, roughness, base color/absorption approximation, and optional volume absorption appropriate to the selected render engine. Assign it to the domain output without clearing unrelated slots unless an explicit replacement policy is supplied. Offer transparent presets such as water, glass-like liquid, oil, and tinted liquid as fully reported numeric values. Configure render-engine-specific refraction only after capability checks. Material creation must not bake or alter solver settings.

### 20. `create_secondary_particle_render_setup`

**Description:** Configure the baked spray, foam, bubble, and tracer systems for efficient viewport and final rendering.

**Implementation details:** Inspect particle systems/datablocks generated by the domain after secondary-particle baking, classify them by Mantaflow role, and configure display amount, material, and render representation with explicit policies. Support instancing a supplied low-poly object or a bounded procedural sphere/material setup, while keeping source instance objects in a helper collection. Never assume generated particle-system names; discover them from the domain and before/after datablock diffs. Return system-to-role mapping and estimated rendered instance count.

### 21. `sample_liquid_simulation`

**Description:** Evaluate a bounded set of frames for preview and return numerical evidence about the liquid result without starting a protected bake.

**Implementation details:** Require `REPLAY` cache mode or already available modular/final cache data. Step frames sequentially with `scene.frame_set`, inspect the evaluated domain modifier/object, and return mesh/particle counts where publicly accessible, world bounds, empty-output frames, domain-boundary proximity, non-finite geometry, and large frame-to-frame count/bounds changes. Restore the original frame in `finally`. Sampling can populate a replay cache and may be slow; report this side effect and never label it a final bake.

### 22. `manage_liquid_cache`

**Description:** Inspect, configure, bake, pause, resume, or explicitly free individual Mantaflow cache stages.

**Implementation details:** Provide actions for status, configure, bake data, bake guides, bake mesh, bake particles, bake all, pause, free data, free guides, free mesh, free particles, and free all. Set `cache_directory`, type (`REPLAY`, `MODULAR`, `FINAL`), formats, frame start/end/offset, and resumable mode only before incompatible stages are baked. Invoke `bpy.ops.fluid.bake_data`, `bake_guides`, `bake_mesh`, `bake_particles`, `bake_all`, matching `free_*`, or `pause_bake` under a correct domain/Object Mode override and verify the domain’s bake-state flags afterward. Enforce stage order and refuse scene-wide generic cache operators. External paths require user authorization, uniqueness, write validation, size/time limits, progress, cancellation, and failure-safe cleanup.

### 23. `remove_fluid_components`

**Description:** Explicitly remove selected domain, flow, or effector modifiers and optionally MCP-owned helper objects or caches.

**Implementation details:** Resolve exact object/modifier pairs and preflight all targets. Remove the `FLUID` modifier through the object modifier collection; remove tagged helpers only when explicitly requested. A domain cache must be freed through `manage_liquid_cache` before its modifier is removed, unless the caller explicitly accepts orphaning external files. Never delete source/render objects or whole collections implicitly. Return removed modifiers/helpers, retained cache paths, and recovery information.

## P2 — Proxy workflows, variants, delivery, and scale

### 24. `create_liquid_proxy_rig`

**Description:** Create low-resolution source or collision proxies that follow high-resolution animated assets for faster, more stable liquid interaction.

**Implementation details:** Build box, capsule, convex-hull, decimated, or explicitly supplied proxy geometry; link it to a dedicated collection; and drive it through parenting or `COPY_TRANSFORMS` with correct inverse matrices. Add FLOW or EFFECTOR settings only to the proxy while preserving the visible source. Support deforming proxies only when intentionally requested and bounded. Tag source/proxy/domain roles and validate world-transform agreement over sampled frames. This is one-way coupling: liquid can collide with a rigid-body-driven proxy, but Mantaflow does not push the rigid body back.

### 25. `duplicate_liquid_setup_variant`

**Description:** Duplicate a complete liquid setup for low/high-resolution tests or controlled solver comparisons without sharing unsafe cache state.

**Implementation details:** Discover members through domain collections and stable MCP IDs, duplicate the domain, flows, effectors, guides, materials, and required animation using explicit linked-versus-copied data policies, and remap modifier collection/object references to the new members. Assign a unique cache directory and start with no baked flags. Disable or hide one variant by explicit policy to prevent overlapping domains from evaluating together. Preserve provenance and validate that no copied domain points to the original cache path or unintended source objects.

### 26. `prepare_liquid_render_mesh`

**Description:** Add non-destructive render finishing to the baked liquid mesh while preserving the simulation domain and cache.

**Implementation details:** Verify that liquid mesh output exists, then configure smooth shading, material assignment, optional restrained Subdivision Surface or corrective Smooth/Laplacian Smooth modifiers, and render visibility in a deliberate post-fluid modifier order. Preserve speed-vector attributes required for motion blur and warn when a modifier destroys them. Offer a duplicate delivery object only when evaluated mesh copying is appropriate; otherwise retain the live domain. Return base/evaluated counts, bounds, material slots, speed-vector availability, and modifier order.

### 27. `export_liquid_simulation`

**Description:** Export a baked liquid mesh and optional secondary particles for DCC, renderer, game-engine, or archive handoff.

**Implementation details:** Require an explicit path, format, frame range, unit/axis convention, component selection, and overwrite confirmation. Prefer Alembic or USD for animated surface meshes after verifying Blender 5.1 exporter capabilities; offer per-frame mesh export only with a bounded range and explicit naming template. Export secondary particles only when the format/backend supports points or converted instances, and disclose conversions. Preserve speed/velocity attributes when supported and state losses otherwise. Use controlled selection/context, verify operator completion and output files, and never delete or modify the source cache.

### 28. `analyze_liquid_performance`

**Description:** Diagnose likely liquid-simulation performance, cache, and stability bottlenecks using bounded evaluation and structural heuristics.

**Implementation details:** Report estimated grid cells, domain volume utilization, flow/effector triangle counts, animated/deforming collider cost, source speed versus cell size/subframes/CFL settings, time steps, particle ranges, mesh up-resolution, secondary-particle settings, viscosity solver use, guide cost, frame range, formats, cache size on disk, and stage status. Optionally measure controlled replay evaluation over a small frame sample and keep timing separate from estimates. Flag oversized domains, tiny features, high mesh scale, excessive secondary particles, slow cache storage, shared cache directories, and invalidated downstream stages. Blender exposes no reliable per-cell Mantaflow profiler through public RNA, so never invent exact peak-memory or remaining-time values.

## Shared implementation contract

All liquid-simulation tools should follow the repository’s production contract:

- Require explicit scene, domain, source, effector, collection, frame range, cache stage, coordinate space, and path inputs where ambiguity matters. Never use current selection as workflow scope.
- Inspect first. Establish modifier roles/order, transforms and scale, evaluated bounds, scene units/gravity, animation, collections, cache type/path/state, and existing outputs before mutation.
- Validate every batch completely before the first mutation. Reject missing or linked read-only data, incompatible modifier roles, unsupported sources, non-finite values, invalid frame ranges, unsafe resolutions/counts, and cache-path collisions.
- Run all `bpy`, dependency-graph, and fluid-operator work on Blender’s main thread. Use RNA for settings and `bpy.ops.fluid` only for bake/free/pause operations that require operators.
- Preserve active object, selection, mode, current frame, scene/view layer, and editor context with `try`/`finally`. Supply a valid Object Mode override and require `{'FINISHED'}`.
- Treat caches as derived but valuable production data. Settings, transforms, modifiers, sources, effectors, boundaries, animation, and frame-range changes can invalidate one or more stages. Never delete or overwrite a bake implicitly.
- Default to non-destructive source objects, proxy objects, live modifiers, and separate render finishing. Do not apply transforms, convert the domain, clear materials, or delete sources/helpers unless explicitly authorized.
- Use unique cache directories per domain and variant. Never silently reuse Blender’s default cache directory across production shots or overwrite files outside a path provided by the user.
- Tag domains, flows, effectors, guides, proxies, materials, and variants with stable simulation UUID, role, schema version, ownership, and source mapping.
- Extend rollback beyond newly created datablocks. In-place modifier/settings changes and external cache directories require explicit snapshots/journals; cache deletion cannot be rolled back through datablock tracking.
- Return JSON-serializable results with changed objects/modifiers/settings, collections, cache state/path, invalidated stages, evaluated evidence, retained live dependencies, warnings, and next required bake stage.
- Bound resolution, estimated cell/particle counts, sampled frames, object counts, cache bytes, bake duration, and export files. Long bake/export operations need progress, cancellation, timeout handling, and clear partial-state reports.
- Add pure tests for schemas, bounds fitting, cell estimates, cache-stage transitions, presets, and path validation, plus Blender 5.1 runtime tests for domain/flow/effector initialization, moving sources, meshes, particles, guiding, cache lifecycle, proxies, and export.

## Established liquid-simulation practices reflected in the plan

- Keep the domain as small and fixed as the shot allows; unused domain volume consumes resolution and cache space.
- Work at plausible scene scale with unit object scale. Cell size, surface thickness, viscosity, source speed, and collider distance all depend on scale.
- Use low-resolution `REPLAY` tests first, then a unique `MODULAR` cache for staged data, mesh, particle, and guide iteration; use `FINAL` only for an intentional all-in-one delivery bake.
- Lock transforms, topology, source animation, solver settings, mesh/particle options, and frame range before final baking. Upstream changes invalidate downstream cache stages.
- Add flow/effector subframes for fast or thin moving geometry before raising global resolution blindly.
- Prefer simple closed flow and collision geometry. Use proxies for high-resolution or deforming assets and keep sources/render meshes separate.
- Enable speed vectors before the data/mesh bake when motion blur or downstream velocity is required.
- Tune particle sampling and mesh radius/smoothing at preview resolution, then review silhouette, volume loss, droplets, and collision leakage at representative final frames.
- Use secondary spray/foam/bubble/tracer particles only when the shot benefits; they add cache and render cost and should have a deliberate rendering strategy.
- Treat viscosity values as scale- and unit-sensitive. Convert dynamic viscosity/density to Blender’s kinematic representation explicitly rather than guessing.
- Version solver settings, random seeds, input animation, domain bounds, cache directories, and Blender version so approved results can be reproduced.

## What should not become separate tools

- Do not expose arbitrary Mantaflow Python or a generic unrestricted `FluidDomainSettings` setter. Many fields are smoke-only, stage-dependent, or unsafe after baking.
- Do not create one tool per liquid solver property, boundary face, secondary-particle type, viscosity preset, cache format, or force type. Use typed patches and discriminated variants.
- Do not split bake, free, pause, and status into separate public tools for every cache stage. One cache lifecycle tool can enforce ordering and authorization consistently.
- Do not duplicate general object creation, transforms, animation, collection, material, or modifier-stack tools where the existing MCP already provides an adequate primitive.
- Do not treat Ocean, Dynamic Paint, Geometry Nodes simulation, particle fluids, smoke/fire, and Mantaflow liquid as one generic “fluid” command. They have different data models and bake contracts.
- Do not combine setup, final baking, render finishing, and export into one opaque command. Failures at each stage require different recovery and user authorization.
- Do not call `bpy.ops.ptcache.bake_all` or clear all caches scene-wide; use the domain-specific `bpy.ops.fluid` lifecycle.
- Do not promise two-way coupling with rigid bodies or exact memory/time predictions that Blender’s public API cannot provide.

## Comparable MCP findings

Research snapshot: 2026-08-29. Repository capabilities can change after this date.

### `blender-ai-mcp`

`ahujasid/blender-mcp` offers scene inspection, arbitrary Blender code, asset integrations, and viewport verification, but its reviewed public server contains no typed Mantaflow liquid tools. A model can script domains and bakes through `execute_blender_code`, but that gives no stable cache-stage state machine, path isolation, resource bounds, operator-context handling, or structured validation. Its useful precedent is inspect-and-verify; production liquid control should not depend on arbitrary scripts.

### `blender-mcp-bridge`

`seehiong/blender-mcp-bridge` has a broad task-oriented surface for modeling, modifiers, materials, animation, architecture, print checks, and reproducible recorded sessions. Its published tool reference reviewed for this plan has no dedicated liquid domain, flow, effector, mesh, secondary-particle, or Mantaflow cache commands. Its global parameters, branches, and replay are valuable for low/high simulation variants, but cache directories and staged bake state must also be first-class.

### `blender-mcp-pro`

`youichi-uda/blender-mcp-pro` advertises broad object, modifier, animation, node, and render functionality, but its reviewed handler tree contains no fluid-physics module. A generic modifier creator is not sufficient because `FluidModifier` settings are role-specific and initialized lazily, while bake/free operations require exact domain context and stage ordering. The proposed surface is therefore distinct rather than a duplicate of its general modifier tools.

### Current repository

This repository already provides strong prerequisites: focused mesh tools, evaluated modifier summaries, main-thread command dispatch, state restoration helpers, transaction tracking, and explicit production constraints. Its liquid gap is complete. Implementation should live in `src/blender_mcp/server/tools/liquid.py` and `src/blender_mcp/bundled/addon/handlers/liquid.py`; read-only inspection/resource estimation/validation commands should be classified correctly, while sampling must be treated as evaluation that can populate replay caches. Long bakes must not block socket/event-loop work without progress and cancellation.

Across the comparable MCPs, the highest-value gap is not raw access to Mantaflow. It is a reliable liquid lifecycle: tight domains, typed sources/effectors, bounded quality settings, unique cache paths, enforced stage order, cache-safe iteration, evaluated validation, and explicit delivery/export.

## Sources

### Official Blender 5.1 documentation

- [FluidModifier API](https://docs.blender.org/api/5.1/bpy.types.FluidModifier.html)
- [FluidDomainSettings API](https://docs.blender.org/api/5.1/bpy.types.FluidDomainSettings.html)
- [FluidFlowSettings API](https://docs.blender.org/api/5.1/bpy.types.FluidFlowSettings.html)
- [FluidEffectorSettings API](https://docs.blender.org/api/5.1/bpy.types.FluidEffectorSettings.html)
- [EffectorWeights API](https://docs.blender.org/api/5.1/bpy.types.EffectorWeights.html)
- [FieldSettings API](https://docs.blender.org/api/5.1/bpy.types.FieldSettings.html)
- [Fluid operators](https://docs.blender.org/api/5.1/bpy.ops.fluid.html)
- [Object and evaluated dependency-graph API](https://docs.blender.org/api/5.1/bpy.types.Object.html)
- [Fluid simulation manual](https://docs.blender.org/manual/en/5.1/physics/fluid/index.html)
- [Fluid domain settings](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/settings.html)
- [Fluid domain cache](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/cache.html)
- [Liquid settings](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/liquid/index.html)
- [Liquid diffusion, viscosity, and surface tension](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/liquid/diffusion.html)
- [Liquid particles](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/liquid/particles.html)
- [Liquid mesh](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/liquid/mesh.html)
- [Fluid flow objects](https://docs.blender.org/manual/en/5.1/physics/fluid/type/flow.html)
- [Fluid effectors](https://docs.blender.org/manual/en/5.1/physics/fluid/type/effector.html)
- [Fluid guides](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/guides.html)
- [Fluid collections](https://docs.blender.org/manual/en/5.1/physics/fluid/type/domain/collections.html)
- [Fluid materials](https://docs.blender.org/manual/en/5.1/physics/fluid/material.html)

### Comparable projects and repository guidance

- [`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) (`blender-ai-mcp`)
- [`seehiong/blender-mcp-bridge`](https://github.com/seehiong/blender-mcp-bridge)
- [`youichi-uda/blender-mcp-pro`](https://github.com/youichi-uda/blender-mcp-pro)
- Repository `AGENTS.md` / `CLAUDE.md` production, safety, transaction, and Blender 5.1 requirements

