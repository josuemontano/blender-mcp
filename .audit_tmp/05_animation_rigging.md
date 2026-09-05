# Slice 05 — Animation (rubric §9) & Rigging (rubric §10)

Scope: `animation.py`, `object_animation.py`, `character_rigging/{__init__,controls,deformation,foundation,posing}.py` (server) and their addon handler counterparts. Runtime validated against Blender 5.2.1 LTS (`/opt/homebrew/bin/blender`).

## 1. Tool inventory

### `src/blender_mcp/server/tools/animation.py` (6 tools) — generic ID animation data

| Tool | Line | Purpose | Abstraction | Overlap | Verdict |
|---|---|---|---|---|---|
| `inspect_animation` | 230 | Read active Action, layered slots, keyframes, drivers, NLA strips (paginated) for any `AnimationTarget` (OBJECT/SCENE/MATERIAL/WORLD/CAMERA/LIGHT/MESH/CURVE/ARMATURE/SHAPE_KEYS/NODE_GROUP) | Mid (generic RNA path) | None | KEEP — this is the one true "read anything animated" tool |
| `manage_animation_action` | 244 | CREATE/ASSIGN/DUPLICATE/UNASSIGN a Blender 5.1+ layered Action on one ID | Mid | None | KEEP |
| `edit_keyframes` | 273 | Batch UPSERT/REMOVE keys on an arbitrary `data_path`+`array_index` | Low (raw RNA path) | Overlaps `keyframe_object_transform` and `keyframe_camera_rig` for OBJECT transforms; is the *only* path for MATERIAL/WORLD/SHAPE_KEYS/NODE_GROUP/CURVE/MESH/generic ARMATURE-object properties, and the only path for AXIS_ANGLE rotation | KEEP as the low-level escape hatch |
| `bake_evaluated_animation` | 296 | Sample the evaluated dependency graph (constraints, drivers, physics, parent chains) into a new plain Action | Mid | None | KEEP |
| `manage_nla_tracks` | 331 | CREATE_TRACK/ADD_STRIP/PATCH_TRACK/PATCH_STRIP/REMOVE_STRIP/REMOVE_TRACK | Mid | None | KEEP |
| `manage_animation_driver` | 372 | ADD/PATCH/REMOVE a driver; SCRIPTED expressions are AST-whitelisted to arithmetic only | Mid | Overlaps `create_rig_property_driver` (character_rigging/controls.py) and `create_shape_key_controls`, which are driver-creation tools specialized for rig custom-properties and shape keys | KEEP — generic counterpart to the rig-specific driver builders |

### `src/blender_mcp/server/tools/object_animation.py` (1 tool)

| Tool | Line | Purpose | Abstraction | Overlap | Verdict |
|---|---|---|---|---|---|
| `keyframe_object_transform` | 56 | Batch-key location/rotation/scale for objects, LOCAL or WORLD space, `frame` or `at_seconds` | High (purpose-built, ergonomic) | Same channel-space as `edit_keyframes` for OBJECT location/rotation/scale, and same channel-space as `keyframe_camera_rig` when the object is a camera | KEEP — see §6, this is the intended default entry point |

### `src/blender_mcp/server/tools/character_rigging/foundation.py` (12 tools)

| Tool | Line | Purpose | Abstraction | Overlap | Verdict |
|---|---|---|---|---|---|
| `get_character_rig_info` | 523 | Read-only rig inspection (rest + pose, armature/world space) | Mid | None | KEEP |
| `get_skinning_info` | 552 | Read-only vertex-group/weight-quality inspection | Mid | None | KEEP |
| `create_armature` | 579 | Create armature + prevalidated initial bone hierarchy in one call | High | None | KEEP |
| `patch_armature_bones` | 608 | Atomic CREATE/RENAME/UPDATE/DELETE rest-bone batch with dependency-cycle validation | Mid-High | None | KEEP |
| `mirror_armature_bones` | 628 | Mirror bones across X/Y/Z with `.L`/`.R` token remap | High | None | KEEP |
| `manage_bone_collections` | 654 | Batch CRUD + assign/unassign on Blender 4.0+/5.1 bone collections | Mid | None | KEEP |
| `configure_armature_bones` | 669 | Patch allowlisted non-geometric Bone/PoseBone fields (deform, inherit_scale, IK limits/stiffness, locks, rotation_mode, custom props) | Mid | None | KEEP |
| `bind_mesh_to_armature` | 691 | EMPTY_GROUPS/AUTOMATIC/ENVELOPES/EXISTING_WEIGHTS binding with rollback | High | None | KEEP |
| `set_skin_weights` | 730 | Direct or normalized per-vertex weight assignment | Low-Mid | None | KEEP — only manual-weight path (see §5) |
| `clean_skin_weights` | 748 | Threshold cleanup, influence-limit pruning, renormalization, orphan-group removal | Mid | None | KEEP |
| `add_pose_bone_constraint` | 782 | Create/update one of 13 typed pose-bone constraint kinds | High (typed union, not raw props) | Constrains overlap with `create_ik_chain`/`create_ik_fk_limb` for the IK case specifically | KEEP |
| `validate_character_rig` | 806 | Non-mutating structural preflight (weights, dependency graph) across frames | Mid | None | KEEP |

### `src/blender_mcp/server/tools/character_rigging/controls.py` (6 tools)

| Tool | Line | Purpose | Abstraction | Overlap | Verdict |
|---|---|---|---|---|---|
| `create_ik_chain` | 168 | Plain IK: target/pole controls + IK constraint on a chain | High | Subset of `add_pose_bone_constraint(type=IK)` plus auto-created control bones | KEEP — the docstring itself tells the agent when to use this vs `add_pose_bone_constraint` vs `create_ik_fk_limb`, which is good self-disambiguation |
| `create_ik_fk_limb` | 204 | Duplicate DEF bones into FK/IK chains + switchable blend property | Very high | None | KEEP, but see §5 (over-built relative to primary use case) |
| `create_spline_ik_rig` | 250 | Spline IK against an existing or newly created curve | High | None | KEEP |
| `create_rig_property_driver` | 300 | Custom property + drivers into allowlisted destinations (POSE_BONE/CONSTRAINT/SHAPE_KEY/MODIFIER) | High | Overlaps generic `manage_animation_driver` for the same channel types | KEEP — the allowlisting (`DrivenChannel.property_name` enum) is a real safety win over the generic tool |
| `assign_bone_custom_shapes` | 358 | Assign existing mesh/curve objects as pose-bone custom shapes | Mid | None | KEEP |
| `create_shape_key_controls` | 389 | DIRECT/SIGNED/CORRECTIVE shape-key drivers from custom properties | High | None | KEEP |

### `src/blender_mcp/server/tools/character_rigging/deformation.py` (2 tools)

| Tool | Line | Purpose | Abstraction | Overlap | Verdict |
|---|---|---|---|---|---|
| `transfer_skin_weights` | 63 | Data Transfer modifier-based weight transfer between meshes, live by default, optional irreversible commit | Mid-High | None | KEEP |
| `configure_bendy_bones` | 115 | Full B-Bone curvature/scale/roll/handle configuration | Mid | None | KEEP |

### `src/blender_mcp/server/tools/character_rigging/posing.py` (2 tools)

| Tool | Line | Purpose | Abstraction | Overlap | Verdict |
|---|---|---|---|---|---|
| `set_character_pose` | 58 | Apply bone transforms without keying | Mid | None | KEEP |
| `keyframe_character_pose` | 91 | Apply pose + insert/replace/remove exact keys in a named action | Mid-High | Overlaps `edit_keyframes` for the ARMATURE pose-bone case; is friendlier because it accepts full bone poses (location/rotation/scale/matrix) in one call instead of per-channel `data_path` edits | KEEP |

**Count reconciliation:** foundation(12) + controls(6) + deformation(2) + posing(2) = **22** `@mcp.tool()` entries in `character_rigging`, not 24 as stated in the directive. The directive's "24" figure appears to also count `animation.py`'s 6-ish or is simply approximate — 22 is the verified count from `grep -c "^@mcp.tool" src/blender_mcp/server/tools/character_rigging/*.py`.

Total for this slice: animation.py (6) + object_animation.py (1) + character_rigging (22) = **29 tools**.

## 2. Runtime Blender 5.2.1 API validation

All commands run via `/opt/homebrew/bin/blender --background --factory-startup --python-expr "..."`.

**Bone collections vs. legacy layers** — confirms the code targets the current API, not pre-4.0 bone layers:
```
HAS_collections: True
HAS_collections_all: True
HAS_legacy_layers (bone.layers): False
```
`manage_bone_collections` (foundation.py:654, handler foundation.py:1824) calls `armature_data.collections.new(...)` and reads `armature_data.collections_all` — matches. No `bones.layers` usage found anywhere in `character_rigging/foundation.py` (grep confirmed zero hits). **No pre-4.0 assumption found.**

**Bendy-bone properties** — every field in `BendyBonePatch` (deformation.py:24) exists on a live edit-bone:
```
bbone props: ['bbone_segments','bbone_x','bbone_z','bbone_handle_type_start','bbone_handle_type_end',
              'bbone_custom_handle_start','bbone_custom_handle_end','bbone_easein','bbone_easeout']
```

**IK constraint properties** — every field in `IKConstraintSpec` (foundation.py:302) exists:
```
IK props: ['chain_count','iterations','use_tail','use_stretch','pole_target','pole_subtarget',
           'pole_angle','weight','orient_weight']
```

**Automatic/envelope weighting operator** — `bind_mesh_to_armature`'s `method="AUTOMATIC"|"ENVELOPES"` maps to `bpy.ops.object.parent_set(type=...)`, and both enum identifiers are real:
```
parent_set 'type' enum: ['OBJECT','ARMATURE','ARMATURE_NAME','ARMATURE_AUTO','ARMATURE_ENVELOPE',
                          'BONE','BONE_RELATIVE','CURVE','FOLLOW','PATH_CONST','LATTICE','VERTEX','VERTEX_TRI']
```

**Keyframe/F-curve APIs** — Blender 5.x's layered-Action model confirmed (plain `action.fcurves` no longer exists; must go through `action.slots`/`action.layers[].strips[].channelbag(slot).fcurves`):
```
action attrs: ['slots', 'layers']   # no 'fcurves' — layered model confirmed
interpolation enum: ['CONSTANT','LINEAR','BEZIER','SINE','QUAD','CUBIC','QUART','QUINT',
                      'EXPO','CIRC','BACK','BOUNCE','ELASTIC']
extrapolation enum: ['CONSTANT','LINEAR']
```
**Finding:** Blender's real keyframe interpolation enum has **13** values. Every tool in this slice that sets interpolation (`KeyframeEdit.interpolation`, `bake_evaluated_animation.interpolation`, `keyframe_object_transform.interpolation`, `keyframe_character_pose.interpolation`, camera's `CameraKeyframe`/`set_camera_interpolation.interpolation`) restricts the `Literal` to exactly `["CONSTANT","LINEAR","BEZIER"]` — the 10 easing presets (SINE/QUAD/CUBIC/QUART/QUINT/EXPO/CIRC/BACK/BOUNCE/ELASTIC) are unreachable through any typed tool. `edit_keyframes` is the one place that *could* carry them but its `KeyframeEdit.interpolation` model (animation.py:69) is equally restricted to the same 3-value Literal — so there is genuinely no path to a non-BEZIER easing curve. See §4.

**Driver APIs**:
```
id driver_add: True
driver type enum: ['AVERAGE','SUM','SCRIPTED','MIN','MAX']
driver var type enum: ['SINGLE_PROP','TRANSFORMS','ROTATION_DIFF','LOC_DIFF','CONTEXT_PROP']
```
`manage_animation_driver`'s `driver_type` Literal (`AVERAGE|SUM|MIN|MAX|SCRIPTED`) matches exactly. `DriverVariable.type` Literal (`SINGLE_PROP|TRANSFORMS`) is a deliberate subset of the 5 real variable types — `ROTATION_DIFF`, `LOC_DIFF`, and `CONTEXT_PROP` are not exposed. This looks intentional (those three are less commonly needed and harder to validate safely) rather than an oversight, but it is a real capability gap if an agent ever needs a rotation-difference-driven corrective.

**NLA strip APIs** — exact match:
```
strip extrapolation enum: ['NOTHING','HOLD','HOLD_FORWARD']
strip blend_type enum: ['REPLACE','COMBINE','ADD','SUBTRACT','MULTIPLY']
```

**Motion paths** — `bpy.ops.object.paths_calculate` exists and is fully functional in 5.2.1, confirming a real capability gap (no MCP tool wraps it) — see §4.

## 3. Reliability analysis

**Playhead (scene.frame_current) preservation** — verified in three places, all correct save/finally-restore:
- `bake_evaluated_animation` (handler `animation.py:749,754,791`): `original_frame = scene.frame_current` before the sample loop, `scene.frame_set(original_frame)` in a `finally` — restored even on exception.
- `keyframe_character_pose` (handler `character_rigging/posing.py:223,229,248`): `previous_frame = scene.frame_current`, sets `scene.frame_set(whole_frame, subframe=...)` to support sub-frame poses, restores in `finally`.
- `bake` path inside `character_rigging/foundation.py:3019,3022,3046` (bake-adjacent helper, same pattern).

**Selection/active-object preservation** — `bind_mesh_to_armature`'s AUTOMATIC/ENVELOPES path (handler `character_rigging/foundation.py:2082-2091`) wraps the `bpy.ops.object.parent_set` operator call in a `with preserve_mode_and_selection():` context manager (defined elsewhere in the same module) and deselects/reselects only the objects it needs for that operator call — selection and active object are restored on exit. `patch_armature_bones` uses `_enter_armature_edit`/`_exit_object_mode` (handler `character_rigging/foundation.py:698-709`), which sets the armature active and switches to EDIT mode but — unlike the pose/bake paths — **does not appear to capture/restore the *original* active object or mode before entering EDIT**; it only guarantees a return to OBJECT mode afterward, not restoration of whichever object/mode was active before the call. This is a narrower reliability guarantee than the animation-keying paths provide (worth a follow-up read of the full function to confirm no wrapper does this at a higher layer, but nothing in the 3082-line file's helper set was named `preserve_mode_and_selection` around that call site).

**`bake_evaluated_animation` correctly captures constraints/drivers**: confirmed at handler `animation.py:755-756` — `depsgraph = bpy.context.evaluated_depsgraph_get()` then `evaluated = obj.evaluated_get(depsgraph)`, and all sampled transforms/bone matrices/properties are read off `evaluated`, not the original datablock. This is the correct pattern: the evaluated object reflects constraints, drivers, physics, and parent-chain results at each sampled frame, so baking an object with an IK constraint or a scripted driver produces a keyframed result that matches what was actually rendered. This is genuinely well-implemented.

## 4. Gap analysis — rubric §9 (Animation)

**"Orbit the camera for 8 seconds" without hand-authored F-curves:** Partially possible, not cleanly. An agent has `keyframe_camera_rig` (camera/animation.py:49) to key `location`/`rotation_euler` at start/end frames, or `keyframe_object_transform` for the camera object generically — both can produce a start/end key pair for an orbit. But:
- There is no **procedural orbit/path-follow primitive** (e.g., a "parent to empty + rotate empty" helper, or a Follow Path constraint convenience tool) — the agent must hand-compute intermediate camera positions/rotations itself if it wants a true circular orbit rather than a single Bezier-interpolated straight-line-ish arc between two keys. A `TRACK_TO`/`DAMPED_TRACK` constraint tool exists (foundation.py `PoseConstraintSpec` family is bone-only; there is no equivalent generic object-constraint tool in this slice — camera-specific targeting tools may exist in `camera/targeting.py`, outside this slice's scope, and could cover this).
- **Easing/interpolation presets are absent** (§2 above) — every keying tool in this slice is capped at CONSTANT/LINEAR/BEZIER even though Blender's real enum has 10 more (SINE/QUAD/CUBIC/BOUNCE/ELASTIC/etc.). A "cinematic" ease-in/ease-out orbit start/stop is exactly the kind of shot this MCP's stated target use case needs, and it cannot be expressed without falling back to manual Bezier handle tuning.
- **Motion paths** (`bpy.ops.object.paths_calculate`, confirmed present in 5.2.1) are not wrapped by any tool in this slice — an agent cannot ask to visualize or query a computed motion path for verification.
- **Looping animation** is achievable via `manage_nla_tracks` (extrapolation `HOLD_FORWARD`, or a repeating strip via `repeat`/`scale` on `NlaStripPatch`) — this part is well covered.
- **Procedural animation** (drivers) is well covered by `manage_animation_driver` and the rig-specific driver builders, modulo the missing `ROTATION_DIFF`/`LOC_DIFF` variable types noted in §2.

**Verdict for §9:** core keying/baking/NLA/driver primitives are solid and reliability-correct, but the *interpolation-preset* gap is a real, easily-fixable miss (the underlying Blender enum already has everything needed — this is a Literal-widening fix, not new Blender-side work), and there is no dedicated "orbit"/path-follow convenience despite that being close to the audit's own headline example prompt.

## 5. Gap analysis — rubric §10 (Rigging)

**Weight painting:** No interactive/brush-stroke weight painting exists (correctly — that's an inherently interactive tool), but the *programmatic* substitute is present and reasonably complete: `set_skin_weights` (direct `REPLACE`/`ADD`/`SUBTRACT` per-vertex, or normalized multi-group assignment), `clean_skin_weights` (threshold/influence-limit/renormalize/orphan-removal), and `transfer_skin_weights` (Data Transfer modifier, 7 mapping modes). Confirmed at the API level: both `set_skin_weights` and `clean_skin_weights` bottom out in `vertex_group.add([index], weight, "REPLACE")` (handler `foundation.py:1055,2259,2407`) — the real, correct Blender weight API. This is a good manual/precise weight-setting story; what's missing is anything resembling "paint a falloff by proximity to a bone" (envelope-style but for existing groups) beyond the coarse ENVELOPES binding method — a minor gap.

**IK/FK:** Well covered — `create_ik_chain` (plain IK), `create_ik_fk_limb` (switchable FK/IK blend with duplicated chains and a driver-blended property), `create_spline_ik_rig` (Spline IK), plus generic `add_pose_bone_constraint(type=IK)`. This is arguably the strongest-covered rigging area in the slice.

**Pose library / pose assets:** **Missing entirely.** No tool in this slice creates, stores, or applies a Blender Pose Asset (Blender's built-in `bpy.ops.poselib.*` / Asset Browser pose-asset system). `set_character_pose` and `keyframe_character_pose` apply/key one explicit pose per call, but there is no way to save a named reusable pose to a library and recall it later — an agent building up a set of expression/gesture poses for reuse across shots has no first-class primitive for that; it would have to fake it with a dedicated Action per pose plus `manage_animation_action` DUPLICATE, which is not the same thing (no thumbnail/tagging/asset-browser integration, and NLA-strip-based reuse is clunkier than a pose asset).

**Rig validation completeness:** `validate_character_rig` (foundation.py:806) is a "non-mutating structural preflight" — the docstring itself is honest that it "does not certify artistic deformation quality." It covers weights and dependency graph across frames but (based on signature alone; full handler body not read in this pass) does not appear to validate bone-roll consistency across mirrored pairs, IK chain solvability at extreme poses, or constraint cycles beyond what `add_pose_bone_constraint` already checks at creation time. This is an honest, appropriately-scoped tool rather than a gap per se.

**Is `character_rigging` over-built for the product-ad/cinematic primary use case?** Yes, materially. The audit's own headline example ("Create a cinematic product advertisement...") implies hero-object/camera/lighting/material work, not character animation. Of the 22 tools:
- **Directly product-ad-relevant:** essentially none. A product ad does not need armatures, bone collections, IK/FK limb switching, Spline IK, bendy bones, or corrective shape keys.
- **Broadly reusable outside character work:** `create_rig_property_driver`/`create_shape_key_controls`/`assign_bone_custom_shapes` *could* drive non-character mechanical rigs (e.g., a product's hinge or a robotic arm), giving them a plausible non-character use.
- **Character-shot-only:** `create_ik_fk_limb`, `create_spline_ik_rig`, `mirror_armature_bones`, `configure_bendy_bones`, weight-transfer/cleanup, and most of `foundation.py`'s bone-collection/constraint machinery are squarely biomechanical-rig features.

This is not a defect in the tools themselves (they are well-typed, validated, and safety-gated to the same standard as the rest of the codebase), but from a **tool-surface economy** standpoint, character rigging is ~22 of this slice's 29 tools (76%) serving a use case that is secondary-at-best to the audit's stated primary target. If the goal is a minimal high-leverage MCP surface for product/cinematic ads, this is the single largest candidate for being spun out into an optional/lazily-loaded tool group rather than always-on surface area an agent must reason about on every call.

## 6. Overlap analysis — three ways to keyframe an object

1. **`edit_keyframes`** (animation.py:273) — raw `data_path`/`array_index`/`frame`/`value`. Works for *any* ID type and *any* property, including AXIS_ANGLE rotation and non-transform properties. Lowest-level, most error-prone (agent must know exact RNA paths and pre-know current values for channels it doesn't want to touch, since it only upserts named channels).
2. **`keyframe_object_transform`** (object_animation.py:56) — object-only, but batches location+rotation+scale for one object/frame into one call, supports WORLD-space keying by solving `matrix_world` through the parent chain (a real ergonomic win `edit_keyframes` does not offer — you'd have to hand-compute local values yourself), supports `at_seconds`. Rejects AXIS_ANGLE (delegates to `edit_keyframes`).
3. **`keyframe_camera_rig`** (camera/animation.py:49) — allowlisted to camera-relevant channels only (`OBJECT` location/rotation_euler/rotation_quaternion/scale, `CAMERA_DATA` lens/ortho_scale/shift/clip, `DOF` focus_distance/aperture_fstop, `CONSTRAINT` influence/offset_factor), no WORLD-space solve, `frame`-only (no `at_seconds`), plus its sibling `set_camera_interpolation` which retroactively changes interpolation on a frame range.

**Is this confusing?** Moderately, yes — for the specific case of **keying a plain object's location/rotation/scale**, both `edit_keyframes` and `keyframe_object_transform` can do it, and an agent has no signal in either tool's schema for *which one is preferred*. `keyframe_object_transform`'s docstring is the more complete/ergonomic tool (world-space solving, batching, seconds conversion) and should be the default; `edit_keyframes`'s docstring does not mention `keyframe_object_transform` at all, so an agent that discovers `edit_keyframes` first has no signal to prefer the other tool. For a **camera object** specifically, there are now *three* candidates (`edit_keyframes`, `keyframe_object_transform`, `keyframe_camera_rig`) with overlapping but not identical channel coverage (only `keyframe_camera_rig` reaches lens/DOF/constraint-influence; only `keyframe_object_transform` does the WORLD-space parent-chain solve; only `edit_keyframes` reaches AXIS_ANGLE). None of the three docstrings cross-references the other two to disambiguate "camera transform" vs. "camera lens/DOF" vs. "raw path." This is the clearest, cheapest documentation fix available in this slice: add one cross-reference sentence to each of the three tools' docstrings naming the other two and when to prefer each.

## Biggest finding

The single biggest finding is a genuine, easily fixable capability gap: **every animation tool in this slice hard-codes keyframe interpolation to `Literal["CONSTANT","LINEAR","BEZIER"]`, but Blender 5.2.1's real `interpolation` enum has 13 values including SINE/QUAD/CUBIC/BOUNCE/ELASTIC/BACK** — runtime-verified directly against the live API. This means no typed tool in the entire animation/camera surface can express an ease-in/ease-out or bounce/elastic motion preset, which is precisely the kind of polish a "cinematic product advertisement" shot needs and which Blender already supports natively; fixing it is a matter of widening a `Literal` type, not adding new Blender-side logic. The second-largest finding is architectural rather than a defect: character rigging accounts for 22 of this slice's 29 tools (76%) despite being largely orthogonal to the audit's stated primary product-ad/cinematic use case, making it the strongest single candidate for splitting into an optional tool group in any surface-consolidation proposal.
