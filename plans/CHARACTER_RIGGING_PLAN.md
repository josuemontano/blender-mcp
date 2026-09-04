## Recommendation

Implement the P0 tools (1–12) first. Together they provide the dependable core of professional character rigging: inspection, armature construction, bone organization, reversible binding, deterministic weight editing, constraints, and validation. Add P1 for reusable control systems, deformation controls, and animation, then P2 for pose assets, retargeting, baking, optional Rigify generation, deformation analysis, and delivery.

The current MCP has no structured character-rigging surface. Its general object and animation facilities are useful prerequisites, but armatures, pose bones, skinning, and rig dependencies are otherwise reachable only through arbitrary Python; `clear_vertex_groups` is destructive and is not a rigging workflow by itself. The new commands should expose Blender's native rig data through typed, batch-oriented operations and keep source meshes, weights, controls, and actions recoverable.

## P0 — Rig foundation and skinning

### 1. `get_character_rig_info`

**Description:** Inspect an armature, its bone hierarchy, controls, constraints, drivers, and animation without changing the scene.

**Implementation details:** Resolve an explicit armature object and return object/data names, local and world transforms, `pose_position`, display settings, users of the armature datablock, actions and NLA tracks, and bounded lists of bone collections, bones, pose bones, custom properties, constraints, drivers, and dependent mesh modifiers. For each bone report parent, children, connected/deform flags, head/tail/roll in armature space, length, inheritance, B-Bone configuration, collection membership, and envelope settings; for pose bones report rotation mode, transforms, locks, IK settings, custom shape, and constraints. Use `Armature`, `Bone`, `PoseBone`, `BoneCollection`, `Object.animation_data`, and dependency-graph evaluation. Paginate large rigs and label every coordinate space. This tool is read-only.

### 2. `get_skinning_info`

**Description:** Inspect how one or more meshes are bound to an armature and summarize vertex-weight quality.

**Implementation details:** Return parent relationship, Armature modifiers and stack positions, modifier target/settings, vertex-group names and locks, deform-bone matches, per-group statistics, unweighted vertices, vertices above a configurable influence limit, non-normalized sums, zero/near-zero assignments, orphan groups, and groups for missing bones. Provide bounded pagination for exact vertex/group memberships using mesh vertex indices and `MeshVertex.groups`; call `update_from_editmode()` when required. Distinguish base-mesh weights from evaluated deformation and do not force frame or pose changes.

### 3. `create_armature`

**Description:** Create a named armature object, optionally with an initial validated bone hierarchy and production display settings.

**Implementation details:** Create data with `bpy.data.armatures.new()` and an object with `bpy.data.objects.new()`, link it to an explicit collection, and assign an explicit world transform. Configure `Armature.pose_position`, axis display, object display type, and in-front display where requested. If initial bones are supplied, validate all names, finite head/tail coordinates, nonzero lengths, parent graph, connectivity, and collection references before entering one controlled Edit Mode session and creating `Armature.edit_bones`. Roll back the object, armature datablock, bones, and collections created by this request on failure. Do not make the armature active or parent meshes unless requested by separate tools.

### 4. `patch_armature_bones`

**Description:** Batch-create, rename, reparent, reshape, or explicitly remove rest bones in an existing armature.

**Implementation details:** Accept a list of typed operations and validate the complete final hierarchy before mutation, including name uniqueness, parent existence, cycles, connected head/parent-tail consistency, nonzero length, mirror metadata, and references from constraints, drivers, vertex groups, and animation. Perform geometry and hierarchy changes through `Armature.edit_bones` in one controlled Edit Mode session; expose head, tail, roll, parent, `use_connect`, `use_deform`, envelope fields, and verified inheritance fields. Use `EditBone.align_roll()` or `align_orientation()` where an explicit axis reference is supplied. Renaming or deletion must require an explicit reference-update policy; deletion and rest-pose edits on animated rigs require confirmation. Return an old/new name map and all affected dependencies.

### 5. `mirror_armature_bones`

**Description:** Mirror an explicit set of bones across an armature-space plane while preserving a predictable hierarchy and left/right naming scheme.

**Implementation details:** Preflight source bones, target-name collisions, parent mappings, connected chains, collection assignments, and the requested `.L`/`.R` or custom token mapping. Mirror head/tail positions in armature space and derive roll from the reflected orientation, or use `bpy.ops.armature.symmetrize` only inside a fully controlled Edit Mode selection/context after verifying its Blender 5.1 behavior. Copy approved bone flags, envelope/B-Bone properties, collection memberships, and optionally pose constraints with remapped subtargets; do not silently mirror animation or vertex weights. Return source-to-target mappings and flag centerline ambiguities.

### 6. `manage_bone_collections`

**Description:** Create, rename, nest, reorder, show/hide, and assign bones to modern Blender bone collections.

**Implementation details:** Use `Armature.collections` and `collections_all` plus `BoneCollection.assign()` and `unassign()`. Support batch operations for collection creation, parent assignment, display/solo state, and explicit bone membership while preserving multi-collection membership by default. Preflight names, nesting cycles, bone references, and collision policy. Removing a collection or clearing memberships requires an explicit destructive action and must report displaced bones. Use Blender 5.1 bone collections only; do not implement legacy armature-layer compatibility.

### 7. `configure_armature_bones`

**Description:** Patch non-geometric bone and pose-channel behavior in batches without entering a rest-pose editing workflow.

**Implementation details:** Use a discriminated schema for `Bone` and `PoseBone` settings rather than arbitrary RNA paths. Support deform/envelope and transform-inheritance flags available outside Edit Mode, pose rotation mode, location/rotation/scale locks, quaternion lock behavior, IK locks, stiffness, stretch, limits, and selected custom properties. Validate every bone and value before applying the first change, preserve unsupported fields, and report old/new values. Keep rest head/tail/roll/parent changes in `patch_armature_bones`, B-Bone curve configuration in `configure_bendy_bones`, and visual controls in `assign_bone_custom_shapes`.

### 8. `bind_mesh_to_armature`

**Description:** Bind explicit mesh objects to an armature using empty groups, automatic weights, envelopes, or an existing-weight workflow.

**Implementation details:** Add or reuse a named `ARMATURE` modifier via `obj.modifiers.new()`, set its `object`, `use_vertex_groups`, `use_bone_envelopes`, `use_deform_preserve_volume`, and stack position, and optionally parent while preserving world transforms with `matrix_parent_inverse`. For empty/existing groups, create only missing deform-bone groups. Automatic or envelope weighting may use `bpy.ops.object.parent_set(type='ARMATURE_AUTO'/'ARMATURE_ENVELOPE')` only with explicit selection, active object, Object Mode, and a valid override; check `{'FINISHED'}` and verify the resulting parent, modifier, and groups. Snapshot and roll back newly added modifiers, groups, and parenting on failure. Never clear existing weights unless an explicit replacement policy is confirmed.

### 9. `set_skin_weights`

**Description:** Assign exact vertex weights to deform groups with deterministic replace, add, or subtract semantics.

**Implementation details:** Accept bounded batches of mesh name, bone/group name, vertex indices, weight, and mode. Validate all meshes, group names, locked state, vertex indices, finite weights in `[0, 1]`, and replacement scope before mutation. Use `Object.vertex_groups` and `VertexGroup.add(indices, weight, mode)`/`remove(indices)`; create missing groups only when requested. Offer an optional per-vertex normalized replacement payload for reproducible external solvers. Preserve non-deform groups and locked weights, record old assignments for rollback, and tell callers to refresh topology indices after any topology-changing operation.

### 10. `clean_skin_weights`

**Description:** Normalize, prune, limit, and repair skin weights on an explicit vertex set while respecting locked and protected groups.

**Implementation details:** Implement deterministic data-level cleanup rather than relying on the user's Paint Mode selection. Support removing weights below a threshold, limiting influences with stable tie-breaking, normalizing all or only deform groups, redistributing around locked groups, removing zero entries, and optionally removing confirmed orphan groups. Preflight the entire mesh and calculate the proposed change before writing it; reject impossible normalization where locked sums exceed one. Return changed vertices, removed assignments, before/after influence histograms, residual unweighted vertices, and untouched protected groups. Never invoke the existing broad clear operation as a shortcut.

### 11. `add_pose_bone_constraint`

**Description:** Add or update a validated constraint on a pose bone for common production rig relationships.

**Implementation details:** Create constraints with `PoseBone.constraints.new(type=...)` and stable names. Provide typed variants for `IK`, `SPLINE_IK`, `COPY_TRANSFORMS`, `COPY_LOCATION`, `COPY_ROTATION`, `COPY_SCALE`, `CHILD_OF`, `DAMPED_TRACK`, `TRACK_TO`, `STRETCH_TO`, `LIMIT_*`, `TRANSFORM`, and `ACTION`, exposing only properties verified for each Blender 5.1 constraint type. Validate object and bone targets, chain lengths, axes, influence, spaces, mix modes, and stack position. For `CHILD_OF`, calculate the inverse needed to preserve the evaluated pose when requested. Reject dependency cycles and return evaluated before/after bone matrices plus the exact configured constraint.

### 12. `validate_character_rig`

**Description:** Run a non-mutating production preflight over armatures, controls, skinning, constraints, drivers, and animation.

**Implementation details:** Detect zero-length bones, hierarchy cycles or unexpected disconnected chains, connected-bone gaps, inconsistent roll or mirror pairs, missing/empty bone collections, duplicate rig IDs, nonunit or negative armature/mesh scale, wrong Armature modifier target/order, unweighted vertices, non-normalized weights, excessive influences, orphan groups, absent deform groups, broken constraint/driver targets, dependency cycles, invalid IK chains or pole placement, invalid custom shapes, shared armature/action data surprises, and rest-pose changes after animation. Evaluate a bounded set of requested frames through the dependency graph and return severity, object/bone/vertex/frame, evidence, and remediation. Structural validation must not claim that deformation or control behavior is artistically correct.

## P1 — Control systems, deformation, and animation

### 13. `transfer_skin_weights`

**Description:** Transfer weights from a source mesh to a target mesh using an explicit spatial or topology mapping policy.

**Implementation details:** Validate source/target meshes, transforms, topology expectations, deform-group filters, and destination replacement policy. Use a `DATA_TRANSFER` modifier or controlled `bpy.ops.object.data_transfer` with vertex-group data and a verified mapping such as topology, nearest vertex, nearest edge interpolation, or nearest face interpolation; set object transform and mix semantics explicitly. A live modifier may be retained for iteration, while committing transferred groups is an explicit destructive boundary with rollback. Preserve locked groups, report coverage and unmapped regions, normalize only when requested, and recommend `validate_character_rig` after transfer.

### 14. `create_ik_chain`

**Description:** Build a conventional IK chain with target and optional pole controls on an existing armature.

**Implementation details:** Validate the contiguous chain, root/end orientation, chain length, pole plane, naming, and collection policies before mutation. Create non-deforming target/pole bones in one Edit Mode batch or use explicit external controls when requested, then add a typed `IK` constraint to the terminal pose bone. Configure `target`, `subtarget`, `pole_target`, `pole_subtarget`, `pole_angle`, `chain_count`, iterations, stretch, and position/rotation weights through `KinematicConstraint`. Derive a stable initial pole position from the limb plane and reject near-collinear ambiguity unless the caller supplies a pole vector. Organize controls separately from deform bones and return all controls and dependencies.

### 15. `create_ik_fk_limb`

**Description:** Create an editable IK/FK limb system with an animator-facing blend property and deterministic snap metadata.

**Implementation details:** Build or validate separate DEF, MCH, FK, and IK chains and bone-collection roles, avoiding duplicate deformation. Drive deform or mechanism bones through named copy-transform constraints whose influences are controlled by one bounded custom property on a designated control bone/object. Create IK target and pole controls through the same validated internals as `create_ik_chain`; configure driver variables with explicit RNA targets and simple allowlisted expressions. Store rest-space matrices and role mappings needed for FK-to-IK and IK-to-FK snapping, but make a pose snap an explicit operation rather than an automatic hidden side effect. Validate evaluated continuity at blend endpoints and roll back the whole generated system on failure.

### 16. `create_spline_ik_rig`

**Description:** Build a spline-IK system for spines, tails, tentacles, hoses, and other multi-bone flexible chains.

**Implementation details:** Validate an ordered chain and either accept an existing curve or create a collision-safe `Curve` with explicit spline points. Add a `SPLINE_IK` constraint to the chain end and configure target, chain count, even divisions, Y-scale mode, XZ-scale mode, bulge, and original-scale behavior using `SplineIKConstraint`. Optionally create hook/control bones or empties and bind curve points to them without converting or applying the source. Keep curve tilt/radius semantics explicit, avoid cyclic rig dependencies, and return the control-to-point mapping and evaluated chain length.

### 17. `configure_bendy_bones`

**Description:** Configure B-Bone segmentation, curvature, easing, scale, roll, and custom handles for smooth deformation chains.

**Implementation details:** Patch verified `Bone`/`EditBone`/`PoseBone` B-Bone fields including `bbone_segments`, display dimensions, handle types, custom handle bones, ease, curve offsets, roll, scale-in/out, and handle-scale/ease flags as supported by Blender 5.1. Validate segment limits, handle targets, chain order, circular dependencies, and deform intent. Custom handles should normally be non-deforming MCH controls in a dedicated collection. Evaluate representative poses and report segment matrices or deformation evidence where accessible; do not promise volume preservation without a mesh test.

### 18. `create_rig_property_driver`

**Description:** Create a custom rig property and safely drive one or more bone, constraint, shape-key, or modifier channels from it.

**Implementation details:** Store the property on an explicit armature object or pose bone, configure default/min/max/soft limits and UI metadata through `id_properties_ui()`, then use `driver_add()` on exact supported data paths. Create typed driver variables and targets for transform channels, single properties, and context properties, with explicit bone targets and spaces. Restrict expressions to a small allowlist of arithmetic forms or use scripted expressions generated from validated presets; reject arbitrary Python. Detect existing drivers and dependency cycles, provide replace/update/error policies, and return the resolved data paths, variable targets, and expression.

### 19. `assign_bone_custom_shapes`

**Description:** Assign reusable custom control shapes and their display transforms to pose bones.

**Implementation details:** Validate shape objects, armature/bone targets, and ownership before setting `PoseBone.custom_shape`, `custom_shape_transform`, translation, Euler rotation, scale XYZ, and wire width fields supported by Blender 5.1. Support batch assignment and collection-based organization of widget objects; keep widgets non-rendering through explicit viewport/render policy without unlinking them from all collections. Reuse a shape datablock by default and never duplicate geometry per bone unless requested. Return shape users and warn about deleted, hidden, or externally linked controls.

### 20. `set_character_pose`

**Description:** Apply a deterministic pose to explicit bones in local, pose, armature, or world space without keyframing it.

**Implementation details:** Accept matrices or typed location/rotation/scale values with an explicit coordinate space and rotation representation. Use `PoseBone.matrix`, `matrix_basis`, parent/rest matrices, and `Object.convert_space()` as appropriate; support Euler, quaternion, and axis-angle modes without silently converting the stored rotation mode. Validate the whole pose batch and constraint interaction before mutation, preserve the current frame, and offer a visual-transform policy for constrained bones. Return assigned and evaluated matrices separately so constraint-driven differences are visible. Do not reset unspecified bones or overwrite animation.

### 21. `keyframe_character_pose`

**Description:** Insert, replace, or remove coordinated pose and rig-property keys on explicit bones and frames.

**Implementation details:** Apply pose values through the same space-conversion path as `set_character_pose`, then use `keyframe_insert()` on exact transform or custom-property data paths. Resolve or create an explicitly named `Action`, respect action-slot/layer APIs in Blender 5.1 through runtime introspection, and never overwrite an unrelated active action implicitly. Support insert-only, replace-existing, and exact-key removal policies plus interpolation and handle updates through `FCurve` keyframe points. Preserve current frame/action state in `try`/`finally` and return the action, curves, and changed keys.

### 22. `create_shape_key_controls`

**Description:** Connect facial, muscle, and corrective shape keys to animator-facing rig controls through bounded properties and drivers.

**Implementation details:** Validate mesh shape-key data, Basis key, target `KeyBlock` names, armature controls, slider ranges, and existing drivers. Create control properties with UI metadata and drive `KeyBlock.value` using typed variables and allowlisted expressions; support one-to-one sliders, signed split shapes, and explicit multi-variable corrective formulas. Keep shape-key creation/sculpting outside this tool, preserve existing key values and drivers unless replacement is requested, and detect cycles. For corrective shapes, store driver/rest-pose metadata and document whether evaluation depends on local rotations, quaternion components, or transforms in another space.

## P2 — Assets, retargeting, analysis, and delivery

### 23. `create_pose_asset`

**Description:** Capture selected rig channels as a reusable Blender pose asset with explicit metadata and bone scope.

**Implementation details:** Resolve an armature, selected bone list, current or supplied pose, asset name, catalog, author, and description. Use Blender 5.1's pose-library/asset operators only after runtime capability inspection and with a valid 3D View/Asset Browser override; otherwise construct the supported Action representation and mark it as an asset through the public ID asset API. Include only requested bones and custom properties, preserve the existing action and frame, and never save an external asset-library file without an explicit path and overwrite authorization. Verify that the resulting asset can be resolved and report its action/datablock and catalog identity.

### 24. `retarget_character_animation`

**Description:** Retarget animation from a source armature to a target rig using an explicit bone map, rest-pose policy, and root-motion policy.

**Implementation details:** Require source-to-target bone mappings and validate hierarchy, rest transforms, coordinate conventions, scale, frame range, and destination action policy. Sample evaluated source pose matrices at a bounded step, derive motion relative to each source rest/parent space, convert it through the corresponding target rest/parent spaces, and apply it to target pose channels. Handle root motion, hips translation, twist bones, missing channels, and scale explicitly; do not infer a humanoid mapping from names without user-approved results. Write to a new Action or NLA track by default, preserve the source animation, reduce keys only as an optional verified post-process, and return per-bone coverage and residual warnings.

### 25. `bake_character_animation`

**Description:** Bake evaluated constraints, drivers, IK/FK, or retargeted motion to a clean action for export or handoff.

**Implementation details:** Require explicit armature, bone/channel scope, frame range, step, visual-keying policy, and destination action. Prefer bounded dependency-graph sampling and pose-space conversion when exact output control is needed; `bpy.ops.nla.bake` may be used only with a controlled selection/mode/context and verified `{'FINISHED'}` result. Bake quaternion channels where appropriate, preserve custom properties when requested, and offer key reduction as a separate tolerance-driven stage. Create a new action by default; clearing constraints, parents, or source actions is a separate destructive action requiring confirmation. Restore frame/action state and report sampled channels and any discontinuities.

### 26. `generate_rigify_rig`

**Description:** Create or generate a Rigify metarig workflow when the optional Rigify extension is installed and enabled.

**Implementation details:** Check add-on/extension state and exact operator availability before exposing the capability. Support creating a known metarig type, applying validated metarig bone transforms and Rigify parameters, and generating a rig into an explicit collection using the Blender 5.1 Rigify API/operators under controlled context. Track the metarig, generated rig, widget collection, scripts, and source mapping as one transaction and detect name collisions before generation. Never make core armature, skinning, or export tools depend on Rigify, never assume a particular generated bone name across versions, and return a clear readiness error when unavailable.

### 27. `analyze_skin_deformation`

**Description:** Evaluate skinned meshes across representative poses or frames and report measurable deformation risks.

**Implementation details:** Sample a bounded frame/pose set through the dependency graph, compare evaluated meshes against a declared rest baseline, and calculate topology-stable metrics such as inverted or near-degenerate faces, large area/edge changes, extreme vertex displacement, localized volume loss, discontinuities across seams, and self-intersection heuristics where computationally bounded. Attribute suspicious regions to influential groups/bones and return vertex/face indices that remain valid only for the inspected base topology. Preserve frame and pose state and avoid populating unrelated caches. These metrics guide review; they cannot certify anatomy, silhouette quality, joint design, or artistic deformation.

### 28. `export_character_rig`

**Description:** Export a character rig, skinned meshes, and selected animation with explicit baking, axis, unit, and compatibility policies.

**Implementation details:** Require an explicit output path, format, object list, action/frame range, unit/axis convention, deform/control-bone policy, shape-key policy, and overwrite confirmation. Support FBX, glTF, or USD only after Blender 5.1 operator/capability verification; create temporary duplicates when baking, stripping controls, triangulating, or renaming is required, and preserve the source rig. Control selection and active-object context, check the export operator result, verify the file exists and is nonempty, and clean temporary objects in `finally`. Return exported objects/actions, format settings, provenance, warnings about unsupported constraints/drivers, and verification performed.

## Shared implementation contract

All character-rigging tools should follow the repository's production contract:

- Require explicit armature, mesh, bone, bone-collection, action, frame-range, and coordinate-space inputs wherever ambiguity matters. Never rely on current selection, active object, mode, or pose position.
- Validate complete batches and dependency graphs before the first mutation. Reject missing/wrong types, non-finite transforms, zero-length bones, cycles, bad indices, invalid ranges, name collisions, locked data, and unsupported Blender capabilities clearly.
- Run all `bpy`, dependency-graph, operator, and animation access on Blender's main thread. Prefer RNA/data APIs; use `bpy.ops` only for workflows Blender exposes solely as operators and provide a valid `context.temp_override`.
- Preserve active object, selection, mode, current frame, pose/rest display, active action, and editor context with `try`/`finally`. Edit bones only in a controlled Armature Edit Mode session and pose channels only in the intended armature context.
- Default to non-destructive construction: live Armature modifiers, separate DEF/MCH/CTRL bones and collections, new actions for retargeting/baking, and retained source meshes/rigs. Never clear weights, remove bones, apply modifiers, overwrite actions, or save/export implicitly.
- Tag generated rigs, bones, controls, widgets, actions, and mappings with a stable rig UUID, role, schema version, ownership, and source identity. Names are human-readable labels, not the sole ownership mechanism.
- Make shared-data behavior explicit. Armature datablocks, meshes, actions, custom shapes, and linked library data may have multiple users; refuse unsafe in-place edits or create an intentional local copy.
- Extend transaction rollback to rest-bone hierarchies, collection membership, pose constraints, custom properties, drivers, weights, parenting, modifiers, and actions. Newly created datablock cleanup alone is insufficient for rig edits.
- Return JSON-serializable results with changed armatures/meshes/bones, old/new name maps, collections, modifiers, constraints, drivers, actions, retained live dependencies, warnings, and evaluated verification evidence.
- Bound bones, vertices, weight assignments, constraints, driver targets, frames, deformation samples, and export work. Long retarget, analysis, bake, and export operations need progress, cancellation, and partial-state reporting.
- Add pure tests for schemas, hierarchy validation, name mirroring, matrix/space conversion, weight cleanup, and mappings, plus Blender 5.1 runtime tests for Edit Mode bone construction, automatic weights, constraints, drivers, IK/FK, retargeting, Rigify capability handling, and export round trips.

## Established character-rigging practices reflected in the plan

- Establish scale, orientation, naming, symmetry, joint centers, and rest pose before skinning; late rest-pose edits can invalidate weights, constraints, and animation.
- Separate deform bones from mechanism and animator-control bones, and organize those roles with Blender 5.1 bone collections rather than legacy layers.
- Keep control hierarchies simple and inspectable. Use custom properties and drivers for deliberate relationships, not opaque dependency webs.
- Use consistent local axes and bone roll across chains. Mirror and validate limbs before building IK, twist, B-Bone, or export systems.
- Bind non-destructively with an Armature modifier. Preserve source meshes and existing vertex groups, and keep modifier order intentional relative to corrective, subdivision, and simulation modifiers.
- Aim for normalized, sparse weights with a pipeline-specific influence limit. Preserve locked weights and review shoulders, hips, elbows, knees, wrists, face, and twist regions in representative poses.
- Use pole controls for stable planar limbs, limit stretch deliberately, and keep IK/FK switching and snapping deterministic at the current frame.
- Prefer quaternion rotation for joints that need broad 3D motion and deliberate Euler orders for animator-facing channels where predictable curves matter.
- Put retargeted and baked animation in new actions/NLA tracks, preserve source motion, and document root motion, scale compensation, rest-pose mapping, and sampling rate.
- Test deformation with the final modifier stack and representative silhouettes, not only with bones in isolation. Structural checks and numeric heuristics still require animator/character review.
- Version rig schemas, bone maps, actions, Blender version, optional add-on versions, and export presets so approved animation can be reproduced.

## What should not become separate tools

- Do not expose generic rig Python execution, arbitrary RNA setters, arbitrary driver expressions, or unconstrained constraint property bags. Typed tools are safer, discoverable, and testable.
- Do not create one public tool per bone property, constraint subtype, IK axis, vertex group, pose channel, or B-Bone field. Use typed discriminated variants and validated batch patches.
- Do not split bone creation, renaming, parenting, head/tail placement, and roll into selection-dependent micro-tools. `patch_armature_bones` should validate and apply the coherent hierarchy once.
- Do not duplicate general object transforms, mesh inspection, collection management, materials, or screenshots where the existing MCP already has adequate primitives.
- Do not merge binding, weight replacement, weight cleanup, retargeting, baking, and export. Their rollback requirements and destructive boundaries are materially different.
- Do not use broad selection-based paint/armature operators when direct data APIs can address explicit bones, vertices, groups, constraints, or actions.
- Do not make Rigify mandatory or assume that it is installed. Native armature, constraint, driver, and skinning tools must remain a complete baseline.
- Do not promise automatic rig generation or deformation quality from mesh shape alone. Joint placement, control design, facial systems, and weighting require artistic intent and review.

## Comparable MCP findings

Research snapshot: 2026-08-29. Repository capabilities can change after this date.

### `blender-ai-mcp`

`ahujasid/blender-mcp` provides broad Blender control through scene inspection, arbitrary Python, rendering, and asset integrations, but its reviewed public tool surface has no typed character-armature, skinning, IK/FK, retargeting, or rig-validation workflow. A model can script those operations through `execute_blender_code`, but that supplies no stable schema, state restoration, hierarchy transaction, weight rollback, or evaluated rig evidence. Its useful precedent is inspect-and-visually-verify; production rigging should not depend on arbitrary code execution.

### `blender-mcp-bridge`

`seehiong/blender-mcp-bridge` exposes a broad task-oriented Blender command set, but its published tool reference reviewed for this plan has no dedicated armature, bone, vertex-weight, pose-constraint, or retargeting commands. Generic object creation, including Empties, can help construct external controls but cannot safely edit armature Edit Mode data or pose dependencies. Its transport and recorded-workflow ideas remain useful, while rig edits need Blender-side transactions and main-thread context handling.

### `blender-mcp-pro`

`youichi-uda/blender-mcp-pro` is the closest comparison: its rigging module includes basic operations corresponding to armature creation, adding/listing/deleting bones, setting a bone property, parenting a mesh, adding a bone constraint, and creating a vertex group. That confirms the value of a structured rigging surface, but the reviewed implementation is fine-grained and context-dependent, permits broad property inputs, and does not provide bone collections, batch hierarchy validation, weight-quality tooling, IK/FK systems, drivers, retargeting, Rigify lifecycle, deformation analysis, or delivery. This plan consolidates low-level edits into safe task-level transactions and adds the missing production lifecycle.

### Current repository

The repository already has useful foundations: mesh/topology inspection, modifier tooling, state-preserving mutation helpers, main-thread command dispatch, structured error propagation, and evaluated geometry summaries. Character rigging is otherwise absent, and `clear_vertex_groups` should remain an explicitly destructive cleanup primitive rather than the basis of skinning. Implementation should live in `src/blender_mcp/server/tools/character_rigging.py` and `src/blender_mcp/bundled/addon/handlers/character_rigging.py`, with shared matrix, transaction, animation, and weight helpers factored by responsibility rather than duplicated.

Across the comparable MCPs, the highest-value gap is not raw access to Blender's armature API. It is dependable production semantics: coherent batch bone editing, modern collection organization, rollback-safe binding and weights, inspectable control systems, explicit coordinate spaces, evaluated validation, source-preserving retarget/bake workflows, and verified interchange.

## Sources

### Official Blender 5.1 documentation

- [Armature API](https://docs.blender.org/api/5.1/bpy.types.Armature.html)
- [EditBone API](https://docs.blender.org/api/5.1/bpy.types.EditBone.html)
- [Bone API](https://docs.blender.org/api/5.1/bpy.types.Bone.html)
- [PoseBone API](https://docs.blender.org/api/5.1/bpy.types.PoseBone.html)
- [BoneCollection API](https://docs.blender.org/api/5.1/bpy.types.BoneCollection.html)
- [ArmatureModifier API](https://docs.blender.org/api/5.1/bpy.types.ArmatureModifier.html)
- [VertexGroup API](https://docs.blender.org/api/5.1/bpy.types.VertexGroup.html)
- [Constraint API](https://docs.blender.org/api/5.1/bpy.types.Constraint.html)
- [KinematicConstraint API](https://docs.blender.org/api/5.1/bpy.types.KinematicConstraint.html)
- [SplineIKConstraint API](https://docs.blender.org/api/5.1/bpy.types.SplineIKConstraint.html)
- [Driver API](https://docs.blender.org/api/5.1/bpy.types.Driver.html)
- [Shape-key data API](https://docs.blender.org/api/5.1/bpy.types.Key.html)
- [ShapeKey API](https://docs.blender.org/api/5.1/bpy.types.ShapeKey.html)
- [Armature operators](https://docs.blender.org/api/5.1/bpy.ops.armature.html)
- [Object operators](https://docs.blender.org/api/5.1/bpy.ops.object.html)
- [NLA operators](https://docs.blender.org/api/5.1/bpy.ops.nla.html)
- [Armatures manual](https://docs.blender.org/manual/en/5.1/animation/armatures/)
- [Armature skinning](https://docs.blender.org/manual/en/5.1/animation/armatures/skinning/)
- [Armature posing](https://docs.blender.org/manual/en/5.1/animation/armatures/posing/)
- [Bendy Bones](https://docs.blender.org/manual/en/5.1/animation/armatures/bones/properties/bendy_bones.html)
- [IK solver](https://docs.blender.org/manual/en/5.1/animation/constraints/tracking/ik_solver.html)
- [Spline IK](https://docs.blender.org/manual/en/5.1/animation/constraints/tracking/spline_ik.html)
- [Rigify](https://docs.blender.org/manual/en/5.1/addons/rigging/rigify/)

### Comparable projects and repository guidance

- [`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) (`blender-ai-mcp`)
- [`seehiong/blender-mcp-bridge`](https://github.com/seehiong/blender-mcp-bridge)
- [`youichi-uda/blender-mcp-pro`](https://github.com/youichi-uda/blender-mcp-pro)
- Repository `AGENTS.md` / `CLAUDE.md` production, safety, transaction, and Blender 5.1 requirements
