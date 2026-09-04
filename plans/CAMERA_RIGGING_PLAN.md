## Recommendation

Implement the P0 tools (1–12) first. Together they cover the dependable core of professional camera work: inspection, camera creation and configuration, deterministic framing and aiming, reversible target/orbit/dolly/crane/path rigs, depth of field, and animation. Add P1 for shot production and reusable rigging, then P2 for VFX interchange and operations that require more scene context or computation.

The MCP should expose these as validated, narrowly scoped tools rather than relying on arbitrary Python execution. Rig builders should create standard Blender objects, constraints, drivers, and animation data so the result remains editable without the MCP.

## P0 — Core production tools

### 1. `get_camera_rig_info`

**Description:** Inspect a camera or camera rig without changing the scene. This is the entry point for planning safe edits and diagnosing existing rigs.

**Implementation details:** Resolve an explicit camera or rig-root name and return camera type, local and world transforms, parent hierarchy, constraints, animation/action, lens settings, sensor and fit, clipping, depth-of-field settings, passepartout, composition guides, render resolution/aspect, active-scene status, and markers that bind the camera. For rig roots, traverse only a bounded descendant depth and report object types, parenting, constraints, drivers, and custom rig metadata. Use `Camera` RNA, `Object.matrix_world`, `Object.constraints`, `Object.animation_data`, `Scene.camera`, and `TimelineMarker.camera`. Paginate animation and child-object data. This tool is read-only.

### 2. `create_camera`

**Description:** Create a named perspective, orthographic, or panoramic camera with explicit transform and optical settings.

**Implementation details:** Prefer `bpy.data.cameras.new()` and `bpy.data.objects.new()` over the context-sensitive `bpy.ops.object.camera_add`. Link the object to an explicit collection. Support world-space location and either Euler rotation, quaternion rotation, or a look-at target, but reject ambiguous combinations. Configure `Camera.type`, `lens`, `ortho_scale`, `sensor_width`, `sensor_height`, `sensor_fit`, `shift_x`, `shift_y`, `clip_start`, and `clip_end`. Panoramic subtypes and engine-specific values must be capability-checked before assignment. Return the collision-safe final names and normalized settings; do not make it active unless requested.

### 3. `configure_camera`

**Description:** Update a camera’s projection, optics, clipping, viewport display, and composition-guide settings without rebuilding its rig.

**Implementation details:** Patch only explicitly supplied fields on `bpy.types.Camera`. Validate positive focal length, clipping order, sensor dimensions, orthographic scale, and finite shift values. Support `lens`, projection type, sensor fit/size, clip planes, lens shift, `dof`, `passepartout_alpha`, `show_passepartout`, `show_safe_areas`, `show_name`, `show_limits`, `show_mist`, and `show_composition_*` fields available in Blender 5.1. Report old and new values. Keep render resolution and camera data separate because one belongs to `Scene.render` and the other to the camera datablock.

### 4. `set_scene_camera`

**Description:** Assign a camera as the active camera for a scene, optionally at a specific timeline marker.

**Implementation details:** Set `scene.camera` directly after validating the object and its data type. If a marker name or frame is supplied, create or reuse a `TimelineMarker` and assign `marker.camera`; reject accidental replacement unless an explicit replace policy is provided. Return the previous active camera, new camera, and marker binding. Avoid `bpy.ops.view3d.object_as_camera`, which is viewport-context dependent.

### 5. `aim_camera`

**Description:** Orient a camera toward an object or world-space point, either immediately or through a live constraint.

**Implementation details:** For a one-time aim, calculate a direction in world space and use `mathutils.Vector.to_track_quat('-Z', 'Y')`, then convert through the parent matrix so the resulting local transform is correct. For a live aim, add or update a named `TRACK_TO`, `DAMPED_TRACK`, or `LOCKED_TRACK` constraint with explicit target/subtarget, track axis, up axis, influence, and owner/target space. Cameras look down local `-Z` with local `Y` up. Preserve existing unrelated constraints and make constraint ordering explicit.

### 6. `create_camera_target`

**Description:** Create or reuse a clean camera target control and optionally connect one or more cameras to it.

**Implementation details:** Create an Empty using `bpy.data.objects.new(name, None)`, give it a useful `empty_display_type` and size, link it to a named rig-controls collection, and place it from an explicit world position or a target object’s evaluated bounds. Add a named tracking constraint only when requested. Store a stable custom-property role such as `mcp_camera_role = "target"`; do not infer ownership solely from names. Reusing a target must be opt-in and type-checked.

### 7. `frame_camera_on_objects`

**Description:** Deterministically position or adjust a camera so explicit objects fit within the frame with a requested margin.

**Implementation details:** Compute a combined world-space bounding box from evaluated dependency-graph objects, including modifier results. For perspective cameras, solve distance from the vertical and horizontal field of view, render aspect, sensor fit, lens shift, and desired margin; use `Camera.angle_x`/`angle_y` where appropriate. For orthographic cameras, solve `ortho_scale`. Permit `move_camera`, `change_lens`, or `change_ortho_scale` policies rather than silently choosing. Optionally aim at the bounds center. Report the bounds, target point, distance, and limiting frame axis. Do not depend on `bpy.ops.view3d.camera_to_view_selected`, which requires a configured viewport.

### 8. `create_orbit_camera_rig`

**Description:** Build an editable turntable/orbit rig suited to character turnarounds, product shots, and hard-surface reviews.

**Implementation details:** Create a root Empty at the pivot, a child boom control offset by radius and elevation, and parent or constrain the camera to the boom. Aim the camera at a target control using a `DAMPED_TRACK` or `TRACK_TO` constraint. Expose radius, azimuth, elevation, roll, lens, and target height through transforms or well-named custom properties with drivers. Use `matrix_parent_inverse` to preserve world transforms. Put helpers in a dedicated collection and tag every created object with rig ID and role. Prefer a simple hierarchy over opaque dependencies.

### 9. `create_dolly_camera_rig`

**Description:** Build a conventional dolly rig with separate root, aim, and camera controls.

**Implementation details:** Construct the rig with Blender data APIs: a root control for translation/yaw, a child camera control for height/pitch/roll, a target control, and a camera with a tracking constraint when requested. Offer local rail direction, starting transform, camera height, target distance, and lens. An optional adapter may call Blender’s Add Camera Rigs extension only after confirming the extension and operator are available, but direct construction should be the stable default. Preserve and report all live controls and constraints.

### 10. `create_crane_camera_rig`

**Description:** Build a crane/jib rig with independently animatable base, arm, head, and target controls.

**Implementation details:** Create a root/base control, height or arm-pivot control, boom, camera head, camera, and optional target. Parent controls in a predictable hierarchy and use limit constraints only when explicit ranges are requested. Arm length and camera height can be driven by custom properties, but drivers should use simple variables and expressions that can be inspected. Support pan, tilt, roll, boom length, and elevation. As with dolly rigs, use the Add Camera Rigs extension only as an optional capability-checked backend.

### 11. `create_camera_path_rig`

**Description:** Attach a camera rig to an existing curve or create a curve-based camera path with deterministic orientation and timing.

**Implementation details:** Use a `FOLLOW_PATH` constraint on a rig root, with the curve object as target, explicit `forward_axis`/`up_axis`, `use_curve_follow`, and fixed-position behavior. Prefer animating constraint `offset_factor` from 0 to 1 because it is easy to inspect and retime; keyframe it with `keyframe_insert`. Optionally add a target control or a second curve for aiming. If creating a path, build a `Curve` datablock and Bézier or NURBS spline from validated world-space points. Do not overwrite existing curve path animation.

### 12. `configure_camera_dof`

**Description:** Configure photographic depth of field using either a focus object or an explicit focus distance.

**Implementation details:** Update `Camera.dof.use_dof`, `focus_object`, `focus_distance`, `aperture_fstop`, `aperture_blades`, `aperture_rotation`, and `aperture_ratio`. Treat focus object and distance as mutually exclusive intent even though Blender retains both fields. Validate positive focus distance and f-stop, and document that the final appearance depends on render engine and sampling. Optionally create/reuse a tagged focus Empty, but do not conflate this with camera aim—the focus and aim targets often differ in professional rigs.

## P1 — Shot, animation, and reusable-rig tools

### 13. `keyframe_camera_rig`

**Description:** Insert or replace coordinated keyframes on camera optics, camera transforms, rig controls, constraints, and depth of field.

**Implementation details:** Accept explicit object/property/value/frame records or a higher-level camera-state payload. Use RNA property assignment followed by `keyframe_insert(data_path=..., frame=..., index=...)`; camera-data paths such as `lens` and `dof.focus_distance` are keyed on the camera datablock, while transforms are keyed on objects. Support replace/insert-only policies, interpolation (`CONSTANT`, `LINEAR`, `BEZIER`), and handle types by editing the resulting `FCurve` keyframe points. Never delete unrelated curves or keys.

### 14. `set_camera_interpolation`

**Description:** Retiming utility for selected rig channels and frame ranges, including constant cuts, linear moves, and eased camera motion.

**Implementation details:** Resolve the owning `Action`, filter `FCurve` objects by exact data path and optional array index, then update only keyframe points inside the requested interval. Support interpolation and Bézier handle types, plus easing where supported. Return matched curves and changed keys. Avoid graph-editor operators; direct animation RNA is deterministic and headless-safe.

### 15. `create_focus_pull`

**Description:** Animate focus between subjects while preserving a live, editable depth-of-field setup.

**Implementation details:** For distance-based pulls, compute camera-space distance to explicit world-space subject points at the requested frames and key `Camera.dof.focus_distance`. For object-driven pulls, create an intermediate focus control and animate its transform, keeping `focus_object` assigned. Support hold, linear, and Bézier transitions. Warn when the requested focus plane passes behind the camera or subjects are outside clip limits.

### 16. `create_dolly_zoom`

**Description:** Create a Vertigo/dolly-zoom move that keeps a chosen subject at approximately constant frame size while camera distance and focal length change.

**Implementation details:** Derive the lens-distance relationship from the camera model, sensor fit, render aspect, subject reference height/width, and framing axis. Keyframe rig translation and `Camera.lens` at explicit start/end frames. For moving subjects, sample evaluated transforms at bounded intervals or offer a driver-based relationship only when it can be expressed robustly. Return the solved focal lengths/distances and flag lens or clipping limits. Do not hide approximation caused by nonplanar subjects.

### 17. `add_camera_shake`

**Description:** Add controllable handheld, vibration, or impact shake without destructively editing authored camera animation.

**Implementation details:** Prefer a dedicated shake parent/control or additive constraints over writing directly into the camera’s primary action. For procedural shake, add Noise modifiers to selected transform `FCurve`s with explicit strength, scale, phase, depth, blend influence, and frame restriction; create base curves if needed. A rig-control approach allows separate translation and rotation amplitudes and clean muting. Seed/phase must be explicit for repeatability. Report exactly where the procedural motion lives and how to disable it.

### 18. `create_camera_markers`

**Description:** Create, update, list, or remove shot markers that switch scene cameras on exact frames.

**Implementation details:** Use `scene.timeline_markers.new(name, frame=...)` and set `marker.camera`. Validate unique names and deterministic collision policies. Batch requests must be fully validated before mutation. Removing or rebinding existing markers is destructive to editorial intent and requires an explicit action and exact marker identifiers. Return the resulting ordered camera-cut map.

### 19. `match_camera_transform`

**Description:** Match a destination camera or rig control to another camera, an object, or an explicit transform while respecting parent spaces.

**Implementation details:** Copy `matrix_world` for an exact world-space match, then derive local matrices automatically through Blender parenting. Allow transform-only, optics-only, or full-camera-data policies. For optics, copy explicit fields rather than replacing the entire datablock unless linked data is requested. Preserve destination constraints unless the request says to mute or account for them. Report whether constraints cause the evaluated transform to differ from the assigned transform.

### 20. `duplicate_camera_rig`

**Description:** Duplicate a complete camera rig for a new shot while controlling whether camera data, actions, and targets are linked or independent.

**Implementation details:** Discover rig members through rig IDs and bounded hierarchy traversal. Preflight all names, duplicate objects with `Object.copy()`, optionally copy datablocks and actions, relink intra-rig parent/constraint/driver targets to the new members, and link objects to a specified collection. Provide explicit policies for camera data, animation, path, and target sharing. Never duplicate arbitrary external constraint targets. Validate that no new rig constraint points accidentally to the old rig unless sharing was requested.

### 21. `add_camera_constraint`

**Description:** Add or update common camera-rig constraints without exposing a generic unsafe property bag.

**Implementation details:** Support a curated set: `TRACK_TO`, `DAMPED_TRACK`, `LOCKED_TRACK`, `FOLLOW_PATH`, `CHILD_OF`, `COPY_LOCATION`, `COPY_ROTATION`, `COPY_TRANSFORMS`, and transform limits. Define typed parameters per constraint, validate object/bone targets and coordinate spaces, use stable constraint names, and allow explicit stack position. For `CHILD_OF`, calculate and set the inverse matrix when preserve-transform is requested. Return evaluated before/after transforms and the complete configured constraint.

### 22. `configure_camera_render_gate`

**Description:** Configure shot-level resolution, pixel aspect, render borders, safe areas, and camera framing guides.

**Implementation details:** Set `Scene.render.resolution_x`, `resolution_y`, `resolution_percentage`, `pixel_aspect_x`, and `pixel_aspect_y` after validating bounded positive values. Support render border through `Scene.render.use_border`, `use_crop_to_border`, and normalized min/max bounds. Configure title/action safe areas on `Scene.safe_areas` where available and camera guide booleans on `Camera`. Keep resolution changes separate and explicit because they affect the whole scene, not just one camera.

### 23. `validate_camera_rig`

**Description:** Run a non-mutating production check on cameras, rigs, and shot bindings and return actionable findings.

**Implementation details:** Detect missing or non-camera scene assignments, broken constraint or driver targets, dependency cycles, duplicate rig IDs, cameras behind their aim targets, invalid clip ranges, extreme lens/sensor values, negative/nonuniform parent scale, invalid path targets, overlapping marker bindings, missing actions, unexpected shared camera data, and focus targets behind the camera. Evaluate representative requested frames through the dependency graph with a strict sample cap. Return severity, object, property, frame, and remediation; never claim visual correctness from structural validation alone.

## P2 — Advanced and VFX tools

### 24. `bake_camera_rig_animation`

**Description:** Bake evaluated rig, constraint, or driver motion to a standalone camera for export, rendering, or handoff.

**Implementation details:** Require an explicit frame range, step, destination policy, and confirmation when overwriting existing destination animation. Prefer creating a new camera/object and sample its evaluated `matrix_world`; decompose carefully and use quaternion rotation to reduce Euler discontinuities. Copy or sample lens, shift, and focus properties when requested. `bpy.ops.nla.bake` may be used only with a fully controlled context; explicit dependency-graph sampling is more deterministic for cameras. Preserve the source rig and report the baked channels and sampling rate.

### 25. `create_stereo_camera_rig`

**Description:** Create a stereo pair or configure Blender’s native stereo camera settings for VFX and immersive work.

**Implementation details:** Prefer the native `Camera.stereo` settings when the render pipeline supports multiview: convergence mode, interocular distance, convergence distance, pivot, pole merge, and spherical stereo fields should be capability-checked. For an explicit left/right pair, build a shared root and aim setup, offset cameras along the local baseline, and use off-axis lens shift rather than toe-in by default to avoid vertical parallax. Tag eye roles and return convergence assumptions. Validate against scene render multiview settings.

### 26. `setup_camera_tracking_solve`

**Description:** Configure and run a bounded motion-tracking camera solve from an existing Movie Clip and existing tracks.

**Implementation details:** Treat this as a staged workflow: inspect clip/tracks, configure `MovieTrackingCamera` intrinsics and solver options, validate a usable frame range and track coverage, run the solve with `bpy.ops.clip.solve_camera` under a valid Clip Editor override, inspect reconstruction error, and optionally set up tracking scene objects with the relevant clip operators. Separate “solve” from destructive scene setup and require explicit confirmation before replacing scene camera/background/compositor state. Operator completion and reconstruction validity must be checked. Long solves need progress/cancellation and a frame/track bound.

### 27. `import_camera_track`

**Description:** Import camera animation and optics from a supported interchange format with explicit axis, unit, and frame mapping.

**Implementation details:** Start with formats that can be parsed deterministically, such as Alembic camera data or a documented JSON schema; add FBX/USD only when Blender 5.1 operator behavior has live verification. Require an explicit path, validate it within the server’s path policy, snapshot objects before import, and identify imports through before/after datablock diffs. Support unit scale, axis conversion, frame offset, action naming, and whether to create or update a camera. Preserve provenance as custom properties. Never report success merely because an import operator returned without raising.

### 28. `export_camera_track`

**Description:** Export a camera or baked rig for DCC, compositor, or matchmove handoff.

**Implementation details:** Require an explicit output path, format, frame range, coordinate convention, and overwrite confirmation. Offer a simple JSON representation containing sampled world matrices and optics as the most predictable baseline; capability-check Alembic/USD/FBX exporters for richer interchange. Bake to a temporary in-scene camera only when necessary and remove it in `finally`. Include frame rate, units, sensor dimensions, lens, shift, clipping, resolution, and pixel aspect. Verify the file was written and return format-specific limitations.

## Shared implementation contract

All camera-rigging tools should follow the repository’s production contract:

- Require explicit object, scene, collection, frame-range, and coordinate-space inputs where ambiguity matters. Never rely on the current selection or active object.
- Validate the complete request before the first mutation. Reject missing objects, wrong object types, non-finite values, invalid ranges, name collisions, and unsupported Blender capabilities clearly.
- Run all `bpy` access on Blender’s main thread. Prefer RNA/data APIs; use `bpy.ops` only when Blender provides no suitable data API and supply a valid `context.temp_override`.
- Preserve active object, selection, mode, current frame, and area context with `try`/`finally`. Evaluating another frame must not leave the user’s timeline displaced.
- Default to non-destructive construction. Create standard objects, constraints, drivers, and actions and retain the source rig when baking or exporting.
- Tag generated members with a stable rig UUID, role, schema version, and ownership metadata. Put helper controls in an explicit collection; do not hide them in unrelated scene collections.
- Make sharing policies explicit for camera datablocks, curves, actions, targets, and constraints. Accidental linked animation is a common production failure.
- Wrap mutations in the MCP transaction layer, preflight batch operations, remove newly created datablocks on failure, and create one named undo checkpoint on success.
- Return JSON-serializable results containing created/changed objects, camera datablocks, rig members and roles, constraints, actions, marker bindings, retained live dependencies, warnings, and verification performed.
- Bound hierarchy traversal, key counts, sampled frames, imported file sizes, and solver work. Long tracking, baking, and interchange tasks need progress and cancellation.
- Add pure tests for validation and camera mathematics plus Blender 5.1 runtime tests for evaluated transforms, parented rigs, constraints, drivers, animation, framing, and import/export round trips.

## What should not become separate tools

- Do not expose a generic `execute_camera_python` or arbitrary RNA-property setter. The repository’s arbitrary-code tool is already unsuitable for the default production surface; typed camera tools are safer and easier for agents to use.
- Do not split perspective, orthographic, and panoramic creation into separate tools. They are projection modes of `create_camera` with different validated fields.
- Do not make one tool per composition-guide toggle, optical property, constraint subtype, interpolation mode, or keyable transform channel. Those belong in typed configuration tools.
- Do not create separate “look at object” and “look at point” tools; they are target variants of `aim_camera`.
- Do not create separate tools for every rig control adjustment. Standard object transforms, custom properties, and `keyframe_camera_rig` cover them.
- Do not combine tracking solve, destructive tracking-scene setup, and export into one opaque command. They have different failure modes and authorization boundaries.
- Do not wrap viewport-only operators for framing, camera switching, or alignment when the same result can be computed through data APIs. Viewport context makes headless automation fragile.

## Comparable MCP findings

### `blender-ai-mcp`

The project is strongest as a general Blender-control bridge: scene inspection, code execution, rendering/screenshots, and broad content manipulation. Camera work can be scripted through its general execution channel, but that does not provide a typed, validated, reversible camera-rig API. The useful lesson is broad scene visibility and visual verification; production camera workflows still need explicit tools and structured results.

### `blender-mcp-bridge`

The bridge pattern demonstrates the value of a small transport layer that forwards commands into Blender. Camera creation and transforms can be expressed through generic commands, but professional concerns—parent-space math, rig ownership, target management, marker cuts, animation sharing, baking, and validation—benefit from dedicated commands. The transport should remain separate from Blender-side camera logic and should never mutate `bpy` off the main thread.

### `blender-mcp-pro`

The broader/pro-oriented tool surface reinforces demand for scene, render, animation, and camera controls, yet general setters and arbitrary scripts leave substantial room for ambiguity. This plan retains that breadth while consolidating camera operations into task-level tools with strict schemas, inspection-before-editing, non-destructive defaults, and verified evaluated results.

### Blender Add Camera Rigs

Blender’s Add Camera Rigs extension is the closest functional comparison for dolly and crane construction. Its editable control hierarchy and camera-target model are useful precedents. The MCP should detect it and may use it as an optional backend, but should not depend on it: extensions may be disabled, operator identifiers and contexts can change, and modal/UI assumptions are poor server contracts. Direct creation with standard objects and constraints gives a stable, inspectable baseline.

Across the comparable MCPs, the clearest gap is not raw access to cameras—generic code can already supply that—but reliable production semantics. The highest-value additions are deterministic framing, aim and focus targets, standard editable rigs, safe keyframing, shot markers, evaluated validation, and bake/interchange tools.

## Sources

### Official Blender 5.1 documentation

- [Camera data API](https://docs.blender.org/api/5.1/bpy.types.Camera.html)
- [Camera depth-of-field API](https://docs.blender.org/api/5.1/bpy.types.CameraDOFSettings.html)
- [Camera stereo API](https://docs.blender.org/api/5.1/bpy.types.CameraStereoData.html)
- [Object API](https://docs.blender.org/api/5.1/bpy.types.Object.html)
- [Constraint API](https://docs.blender.org/api/5.1/bpy.types.Constraint.html)
- [Track To constraint](https://docs.blender.org/api/5.1/bpy.types.TrackToConstraint.html)
- [Damped Track constraint](https://docs.blender.org/api/5.1/bpy.types.DampedTrackConstraint.html)
- [Follow Path constraint](https://docs.blender.org/api/5.1/bpy.types.FollowPathConstraint.html)
- [Timeline marker API](https://docs.blender.org/api/5.1/bpy.types.TimelineMarker.html)
- [FCurve API](https://docs.blender.org/api/5.1/bpy.types.FCurve.html)
- [Keyframe API](https://docs.blender.org/api/5.1/bpy.types.Keyframe.html)
- [Movie tracking API](https://docs.blender.org/api/5.1/bpy.types.MovieTracking.html)
- [Clip operators](https://docs.blender.org/api/5.1/bpy.ops.clip.html)
- [Camera object documentation](https://docs.blender.org/manual/en/5.1/render/cameras.html)
- [Camera rigs documentation](https://docs.blender.org/manual/en/5.1/addons/rigging/add_camera_rigs.html)
- [Camera view and framing](https://docs.blender.org/manual/en/5.1/editors/3dview/navigate/camera_view.html)
- [Depth of field](https://docs.blender.org/manual/en/5.1/render/cameras.html#depth-of-field)
- [Motion tracking camera solve](https://docs.blender.org/manual/en/5.1/movie_clip/tracking/clip/solving.html)

### Comparable projects and repository guidance

- [`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) (`blender-ai-mcp`)
- [`iwk2121/blender-mcp-bridge`](https://github.com/iwk2121/blender-mcp-bridge)
- [`BlenderMCP/blender-mcp-pro`](https://github.com/BlenderMCP/blender-mcp-pro)
- [Blender Add Camera Rigs source](https://projects.blender.org/extensions/add_camera_rigs)
- Repository `AGENTS.md` / `CLAUDE.md` production, safety, transaction, and Blender 5.1 requirements

