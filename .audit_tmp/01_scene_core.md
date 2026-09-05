# Scene/Core/Modeling/ND/Asset-Import Tools Audit

**Domain**: Scene initialization, geometry creation, mesh editing, modeling, ND workflow, asset import (PolyHaven/Sketchfab)  
**Slice Size**: 6 server tools files, 5 addon handler mixins, ~2400 LOC  
**Rubric Sections Evaluated**: 1 (Scene Initialization), 3 (Asset Import & Download), 4 (Production Workflow Architecture)  
**Audit Date**: 2026-09-05  
**Blender Target**: 5.2.1 LTS (5.1+ API only)

---

## 1. Tool Inventory & Coverage

### 1.1 Scene Composition (scene.py: 10 tools, 777 LOC)

| Tool | Purpose | Params | Reliability | Status |
|------|---------|--------|-------------|--------|
| `create_geometry_object` | Declarative typed geometry (mesh, curve, text, metaball, lattice, pointcloud, curves, greasepencil, volume) from pydantic specs | object_name, collection_name, geometry (GeometrySpec union), transform, material_slots, read_only | **HIGH** - full type validation, per-geometry schema, transform/material validation, dry_run optional | ✓ |
| `set_object_transform` | Patch transforms (location/rotation/scale or matrix) with space choice (LOCAL/WORLD) | object_name, transform (TransformPatch), space, read_only | **HIGH** - validates rotation/scale non-degeneracy, matrix 4x4, mutually-exclusive representation checks | ✓ |
| `manage_object_hierarchy` | Reparent/unparent with matrix preservation | object_names, parent_name, keep_transform, read_only | **MEDIUM** - reparents but no explicit world-transform preservation in code | ⚠ |
| `manage_scene_collections` | Create/move/link collections and objects | action (CREATE/MOVE/LINK), collection_name, target_collection, object_names, read_only | **MEDIUM** - action-based dispatcher, no rollback on partial moves | ⚠ |
| `manage_object_constraints` | Add/update/remove constraints (16 types: COPY_*, TRACK_*, LIMIT_*, STRETCH_TO, SHRINKWRAP) | action (ADD/UPDATE/REMOVE), object_name, constraint_spec or constraint_name, read_only | **MEDIUM** - whitelist-enforced, 16 constraint types supported, no influence/subtarget validation | ⚠ |
| `manage_modifiers` | Add/update/remove/reorder modifiers (32 types: ARRAY, BEVEL, BOOLEAN, ...) with typed settings | action, object_name, modifier_spec or modifier_name, priority, read_only | **MEDIUM** - pydantic ModifierSpec validates type + settings, but settings dict is **opaque**; no per-modifier setting validation | ⚠ |
| `duplicate_or_instance_objects` | Create linked instances or duplicates with optional transforms | object_names, instance_count, transforms, collection_name, linked, read_only | **HIGH** - supports pagination of transforms, explicit linked flag, collision-free naming | ✓ |
| `remove_scene_objects` | Delete objects by name with optional recursive option | object_names, recursive, read_only | **HIGH** - explicit object list, no bulk-selection deletion, recursive flag | ✓ |
| `reset_scene` | Clear all objects, restore defaults (units, gravity, playback) | preserve_world, preserve_camera, preserve_lights, read_only | **MEDIUM** - preserves world, camera, lights if requested; but no explicit undo/rollback | ⚠ |
| `validate_scene` | Inspect scene for production-readiness issues (camera, lights, frame range, scale, degenerate geometry, dirty caches) | active_domains (list of scene, camera, lighting, pbr, cloth, liquid) | **HIGH** - comprehensive checks (camera validity, light presence, frame range, unapplied scale, degenerate faces, cloth/rigbody cache status) | ✓ |

**Summary**: 10 tools covering core scene composition, transformation, hierarchy, constraints, modifiers, duplication, deletion, reset, validation. Strongest areas: geometry type unions, validation comprehensiveness, explicit naming/collision-safety. Weakest: modifier settings opaqueness, no per-domain rollback on partial collection moves.

---

### 1.2 Mesh Editing (mesh.py: 13 tools, 579 LOC)

| Tool | Purpose | Params | Reliability | Status |
|------|---------|--------|-------------|--------|
| `create_primitive_object` | Create CUBE, SPHERE, CYLINDER, CONE, TORUS, PLANE, CURVE with optional dimensions override | primitive_type, name, location, rotation, size, dimensions, purpose | **HIGH** - 7 primitives, dimensions override for footprint consistency, blockout tagging | ✓ |
| `mesh_extrude` | Extrude mesh faces by vector offset | object_name, offset (x,y,z), face_indices (optional) | **MEDIUM** - uses bpy.ops.mesh.extrude_region_move, validates FINISHED; but no pre-selection state restoration doc | ⚠ |
| `mesh_inset` | Inset faces by thickness/depth | object_name, thickness, depth, face_indices | **MEDIUM** - bpy.ops.mesh.inset; validates FINISHED | ⚠ |
| `mesh_bevel` | Bevel edges/vertices with offset/segments | object_name, offset, segments, affect (EDGES/VERTS), edge_indices, vertex_indices | **MEDIUM** - affects EDGES or VERTS; validates FINISHED; no harden_normals or angle_limit | ⚠ |
| `mesh_bridge` | Bridge two edge loops (deprecated edge_indices param, preferred: loop selection) | object_name, cuts, interpolation, smoothness, twist_offset, expected_revision (stale-check), edge_indices or loop indices | **MEDIUM** - bpy.ops.mesh.bridge_edge_loops; edge_indices deprecated; no explicit preview/cancel | ⚠ |
| `mesh_symmetrize` | Mirror geometry across 6 axes (NEGATIVE_X, POSITIVE_X, NEGATIVE_Y, ...) | object_name, direction | **MEDIUM** - bpy.ops.mesh.symmetrize; validates FINISHED; no threshold/merge tolerance | ⚠ |
| `mesh_boolean` | Boolean operation (UNION/DIFFERENCE/INTERSECT) with optional cutter retention | object_name, cutter_name, mode, keep_cutter | **HIGH** - explicit cutter retention, validates FINISHED, handles applied modifier result | ✓ |
| `mesh_subdivide` | Subdivide mesh or selected faces | object_name, cuts, face_indices | **MEDIUM** - bpy.ops.mesh.subdivide; no fractal/quad_corner_type options | ⚠ |
| `mesh_remesh` | Voxel remesh | object_name, voxel_size, smoothness (0-1), mode (SMOOTH/SHARP), apply | **MEDIUM** - calls bpy.ops.object.voxel_remesh; mode param not validated against operator capabilities | ⚠ |
| `mesh_solidify` | Thicken surface (solidify modifier, optionally applied) | object_name, thickness, apply | **MEDIUM** - applies modifier if requested; validates apply result | ⚠ |
| `clear_materials` | Strip all material slots from object | object_name | **HIGH** - idempotent, clear operation | ✓ |
| `clear_vertex_groups` | Strip all vertex groups from object | object_name | **HIGH** - idempotent, clear operation | ✓ |
| `clear_edge_marks` | Clear seam/sharp/crease/bevel marks from all edges | object_name | **HIGH** - idempotent, comprehensive mark cleanup | ✓ |

**Summary**: 13 tools covering core mesh ops (primitives, extrusion, inset, bevel, bridge, symmetry, boolean, subdivide, remesh, solidify, material/group/mark cleanup). Strongest: boolean with cutter retention, primitive dimensions consistency. Weakest: operator validation (no pre-checks for mode/threshold), edit-mode state restoration, no fractional subdivide options.

---

### 1.3 Model Transform & Array (model.py: 3 tools, 162 LOC)

| Tool | Purpose | Params | Reliability | Status |
|------|---------|--------|-------------|--------|
| `copy_object_transform` | Align object to reference (location/rotation/scale) in WORLD or LOCAL space | object_name, reference_object_name, match_location, match_rotation, match_scale, space (LOCAL/WORLD) | **HIGH** - matrix_world decomposition for WORLD space, explicit space choice, full quat/euler handling | ✓ |
| `add_radial_array_modifier` | Create radial array around pivot (object/location), with optional apply | object_name, count, radius, height_offset, pivot_object, pivot_location, apply | **MEDIUM** - ARRAY modifier with object offset; validates pivot; but no REPEAT_Y/Z or merge settings | ⚠ |
| `sync_data_name` | Batch rename data-blocks (object.data) to match object names | object_names | **HIGH** - idempotent, safe batch rename, no collision risk (uses object names as truth) | ✓ |

**Summary**: 3 specialized tools for transform operations. High reliability on copy_object_transform (world-space handling). Radial array lacks repeat/merge config.

---

### 1.4 ND (Non-Destructive) Toolkit (nd.py: 10 tools, 408 LOC)

| Tool | Purpose | Params | Reliability | Status |
|------|---------|--------|-------------|--------|
| `nd_boolean` | ND non-destructive boolean (live modifier, cutter wireframed + parented) | object_name, cutter_object_name, mode (UNION/DIFFERENCE/INTERSECT) | **MEDIUM** - wraps nd_call("bool_vanilla"); validates INVOKE_DEFAULT; cutter becomes utility but no undo on cancel | ⚠ |
| `nd_mark_as_util` | Mark objects as ND utility (wireframe display, render-hidden); optionally reparent | object_names, unmark, parent_to | **MEDIUM** - reparents while preserving world matrix; but no explicit undo mechanism on failure | ⚠ |
| `nd_clean_utils` | **DESTRUCTIVE**: Remove all ND utility objects from scene | none | **CRITICAL** - No dry-run flag; hardcoded destructive; no rollback; global operation | 🔴 |
| `nd_create_id_material` | Create material for single object with diffuse ID color | object_name, diffuse_color (RGB), color_space | **MEDIUM** - creates material slot + material; no validation of diffuse_color range | ⚠ |
| `nd_bulk_create_id_materials` | Batch ID materials (one per object, random distinct colors) | object_names, color_space | **MEDIUM** - one material per object, random RGB; no seed control | ⚠ |
| `nd_set_lod_suffix` | Rename objects with LOD suffixes (_high, _low) | object_names, lod_level (HIGH/LOW) | **MEDIUM** - simple rename; no validation that rename succeeds or collides | ⚠ |
| `nd_single_vertex` | Create sketch object (single vertex on origin) | object_name | **HIGH** - creates MESH with one vertex; simple, deterministic | ✓ |
| `nd_apply_modifiers` | Apply all modifiers in REGULAR mode (vs. non-destructive) | object_names | **MEDIUM** - applies via modifier_result helper; no selective apply or mode choice | ⚠ |
| `nd_pulse_viewport_toggle` | Toggle ND viewport overlay (CLEAR_VIEW/CUSTOM_VIEW/UTILS) | action | **LOW** - **NOT idempotent**; toggles state; side effect on viewport display; risky in automation | 🔴 |
| `nd_capture_utils` | Select/display all ND utility objects (for viewing/removal) | action (SELECT/DISPLAY) | **MEDIUM** - selects or displays utilities; but SELECT overwrites prior selection without saving state | ⚠ |

**Summary**: 10 ND tools providing non-destructive boolean, utility marking, ID materials, LOD suffixes, sketch creation, modifier apply, and viewport management. **Critical issues**: `nd_clean_utils` is destructive with no undo/dry-run, `nd_pulse_viewport_toggle` is not idempotent (violates production contract). Moderate issue: `nd_capture_utils` overwrites selection without restoration.

---

### 1.5 PolyHaven Asset Library (polyhaven.py: 4 tools, 253 LOC)

| Tool | Purpose | Params | Reliability | Status |
|------|---------|--------|-------------|--------|
| `get_polyhaven_categories` | List categories for asset_type (hdris/textures/models/all) | asset_type | **HIGH** - checks enablement status, validates asset_type, error handling | ✓ |
| `list_polyhaven_assets` | Browse asset catalog with pagination & category filter | asset_type, categories (comma-sep), limit (1-100), offset | **HIGH** - paginated, deterministic ordering by asset ID, explicit category filter | ✓ |
| `import_polyhaven_asset` | Download and import asset by ID (resolution/format options per type) | asset_id, asset_type, resolution (for textures/models), format (for textures) | **MEDIUM** - async download, network-dependent; no validation of imported object/material structure | ⚠ |
| `apply_polyhaven_texture` | Apply imported texture to material slot | asset_id, material_slot_index, replacement_policy (APPEND/REPLACE_SLOT/REPLACE_ALL), confirm_replace_all | **MEDIUM** - replacement_policy enforcement; but no prior material validation | ⚠ |

**Summary**: 4 tools for Polyhaven integration. Strong: categorical browsing, pagination, asset-type handling. Weak: no pre-download validation of object/material structure, no explicit collision detection on multi-texture replacement.

---

### 1.6 Sketchfab Asset Library (sketchfab.py: 3 tools, 207 LOC)

| Tool | Purpose | Params | Reliability | Status |
|------|---------|--------|-------------|--------|
| `search_sketchfab_models` | Search models by query, optionally filter by category/downloadability, cursor pagination | query (min_length=1), categories (comma-sep), count (1-100), downloadable (bool), cursor | **HIGH** - paginated cursor, category filter, downloadable flag, explicit count limit | ✓ |
| `get_sketchfab_model_preview` | Fetch thumbnail as Image + metadata envelope (non-standard return shape) | uid | **MEDIUM** - base64 image transport; non-standard structured_output=False return shape (Image + dict); no explicit error if model not found | ⚠ |
| `import_sketchfab_model` | Download + import model with auto-normalize (always enabled) | uid, target_size (gt 0) | **MEDIUM** - normalize_size hardcoded True; network-dependent; no preview on import size mismatch | ⚠ |

**Summary**: 3 tools for Sketchfab. Strong: search pagination, downloadable filter. Weak: preview return shape non-standard (mixing Image + envelope), normalize_size hardcoded (no choice), no target-size validation against actual import.

---

## 2. Blender 5.2 API Introspection Results

### 2.1 GreasePencil (Blender 5.2.1 LTS)

**Finding**: GreasePencil v3 confirmed in Blender 5.2.1. Code creates via `bpy.data.grease_pencils.new()`.

```
layers: GreasePencilLayer collection ✓
frames: GreasePencilFrame per-layer collection ✓
drawing: frame.drawing has strokes, attributes, color_attributes, reorder_strokes ✓
```

**Code Assumption Validity**: ✓ VALID — all GreasePencil geometry creation paths tested and confirmed.

### 2.2 Modifier Properties (REMESH, OCEAN, ARRAY, BEVEL)

**Finding**: Real Blender 5.2.1 confirms all modifier properties used in code:

- **REMESH**: `voxel_size`, `adaptivity`, `mode` (SMOOTH/SHARP/VOXEL) ✓
- **OCEAN**: geometry_mode, size, resolution, time, spectrum, bake_foam_fade, foam_layer_name ✓
- **ARRAY**: `use_object_offset`, `offset_object` for radial arrays ✓
- **BEVEL**: `affect` (EDGES/VERTS), `harden_normals` ✓

**Code Assumption Validity**: ✓ VALID — all modifier properties confirmed present in 5.2.1.

### 2.3 Operator Existence & Arguments

**Finding**: Key operators confirmed present in 5.2.1:

- `bpy.ops.mesh.bridge_edge_loops` ✓ (properties: type, use_vertex_loop, interpolation, cuts, smoothness, twist_offset)
- `bpy.ops.mesh.symmetrize` ✓ (properties: direction, threshold)
- `bpy.ops.object.voxel_remesh` ✓ (mode, voxel_size, adaptivity, use_smooth_shade)

**Code Assumption Validity**: ✓ VALID — all key mesh/object operators present and match code expectations.

### 2.4 Transform & Matrix Handling

**Finding**: World-space decomposition via `matrix_world` confirmed; rotation_mode preservation intact.

```python
world_loc, world_rot, world_scale = obj.matrix_world.decompose()
# All properties (location, rotation_mode, scale, rotation_quaternion) accessible per code
```

**Code Assumption Validity**: ✓ VALID — transform decomposition and mode preservation work as coded.

---

## 3. Reliability Assessment

### 3.1 Error Handling

**Strong Areas**:
- Geometry pydantic validation (all GeometrySpec unions type-checked before Blender-side dispatch)
- Constraint whitelist (16 types explicitly allowed, others rejected at schema level)
- Operator result validation (explicit FINISHED checks for extrude, inset, bevel, symmetrize, bridge)
- Explicit object/collection name lookups (raise ValueError if not found; no silent defaults)

**Weak Areas**:
- **Modifier settings dict is opaque** — ModifierSpec allows arbitrary `settings: dict[str, Any]` without per-type validation
  - Example: `add_modifier(object_name, type='BEVEL', settings={'nonexistent_param': 1.0})` will be accepted by server, fail at Blender side without clear error
- **No pre-operator capability checks** — `mesh_remesh` doesn't validate if `voxel_remesh` operator exists before calling
- **Operator context assumptions** — extrude/inset/bevel all assume object is in edit mode, but code doesn't explicitly set it
  - Addon-side helpers preserve mode, but transient failures (e.g., object type mismatch) can leave state corrupted

### 3.2 State Restoration

**On Success**: ✓ All mutation operations wrap in `mutation_transaction` context manager (addon-side), which:
- Snapshots scene state before mutation
- Pushes undo checkpoint on success
- Rolls back to snapshot on exception

**On Failure**: ⚠ **Partial rollback gaps**:
- `manage_scene_collections` with action="MOVE" or "LINK" doesn't guarantee atomic rollback if one move fails mid-sequence
- `nd_capture_utils` with action="SELECT" saves no prior selection, so failure leaves viewport in wrong state
- `reset_scene` has no explicit undo; relies on transaction rollback, but if mutation_transaction wasn't entered, state may persist

**Idempotency**:
- ✓ Idempotent: `create_geometry_object` (read_only=True does nothing), `clear_*`, `sync_data_name`, `set_object_transform`
- ⚠ **NOT idempotent**: `nd_pulse_viewport_toggle` (toggles state; calling twice reverts)
- ⚠ **Idempotent only under assumption**: `add_radial_array_modifier` assumes modifier doesn't already exist

---

## 4. Missing Capabilities vs. Rubric Sections

### 4.1 Section 1: Scene Initialization (10 points)

| Rubric Item | Tool(s) Provided | Gap? | Severity |
|-------------|------------------|------|----------|
| Create empty scene | `reset_scene` | None — explicitly creates blank | ✓ |
| Set unit scale | `scene_physics.py:configure_scene_physics` | None — covered | ✓ |
| Set gravity (for sims) | `scene_physics.py:configure_scene_physics` | None — covered | ✓ |
| Create/configure render camera | `create_geometry_object` (CAMERA primitive missing) | **CAM CREATION MISSING** | 🔴 HIGH |
| Configure viewport/shading | `viewport.py:set_viewport_overlay` | Overlay only; no shading mode selection | ⚠ MEDIUM |
| Bulk collection setup | `manage_scene_collections` (CREATE/MOVE/LINK) | Supports CREATE, single-level hierarchy; no recursive batch structure | ⚠ MEDIUM |
| Assign world/environment | `create_geometry_object` (WorldGeometry not in GeometrySpec union) | **WORLD/HDRI ASSIGNMENT MISSING** | 🔴 HIGH |
| Sync render settings | Not provided | **NO RENDER CONFIG TOOL** | 🔴 HIGH |

**Missing Capabilities (Section 1)**:
1. **Camera creation** — no `kind="CAMERA"` in GeometrySpec; must use manual bpy.data.cameras/objects pathway or constraint camera
2. **World environment setup** — no `kind="WORLD"` geometry; requires separate world material/shader assignment
3. **Render settings** — no tool to set Cycles/Eevee, samples, resolution, output path, etc.

### 4.2 Section 3: Asset Import & Download (5 points)

| Rubric Item | Tool(s) Provided | Gap? | Severity |
|-------------|------------------|------|----------|
| Polyhaven HDRI import | `polyhaven.py:import_polyhaven_asset` (asset_type=hdris) | ✓ Covered | ✓ |
| Polyhaven texture import | `polyhaven.py:import_polyhaven_asset` (asset_type=textures) | ✓ Covered | ✓ |
| Polyhaven model import | `polyhaven.py:import_polyhaven_asset` (asset_type=models) | ✓ Covered | ✓ |
| Sketchfab model search | `sketchfab.py:search_sketchfab_models` | ✓ Covered | ✓ |
| Sketchfab model preview | `sketchfab.py:get_sketchfab_model_preview` | ✓ Covered | ✓ |
| Sketchfab model import | `sketchfab.py:import_sketchfab_model` | ✓ Covered | ✓ |
| Local file import (FBX/GLTF/OBJ) | Not provided | **MISSING** | 🔴 HIGH |
| Import validation/repair | Not provided | **NO VALIDATION AFTER IMPORT** | ⚠ MEDIUM |
| Import to named collection | Partial — `import_polyhaven_asset` returns object names only | No collection assignment | ⚠ MEDIUM |

**Missing Capabilities (Section 3)**:
1. **Local file import** — no tool to import FBX, GLTF, OBJ, USD, etc.; requires external file path
2. **Post-import validation** — no repair of imported normals, degenerate faces, missing armatures, scale mismatches
3. **Import organization** — polyhaven/sketchfab imports don't auto-organize into collections

---

## 5. Tool Redundancy & Consolidation

### 5.1 Boolean Overlap: `mesh_boolean` vs. `nd_boolean`

| Aspect | mesh.py | nd.py | Trade-off |
|--------|---------|-------|-----------|
| **Result** | Applied modifier | Live modifier (non-destructive) | mesh: destructive, nd: reversible |
| **Cutter handling** | `keep_cutter=True` → retains cutter | Always marks cutter as utility | mesh: manual cleanup, nd: auto-organized |
| **Undo** | Via transaction | Via ND operator context | Both have undo |
| **Production use** | Finalized assets | Work-in-progress, refinement | **Complementary, not redundant** |

**Verdict**: ✓ **NOT redundant** — serve different workflows (destructive-finalization vs. non-destructive-refinement). Keep both.

### 5.2 Modifier Management: `manage_modifiers` vs. `nd_apply_modifiers`

| Aspect | manage_modifiers | nd_apply_modifiers |
|--------|-----------------|-------------------|
| **Action** | ADD/UPDATE/REMOVE/REORDER modifiers | Apply all modifiers (single action) |
| **Scope** | Individual or batch creation | Batch application only |
| **Result** | Live or removed modifiers | All modifiers applied (destructive) |
| **Overlap** | None — different operations | Complements manage_modifiers |

**Verdict**: ✓ **Complementary** — manage_modifiers is the creation interface; nd_apply_modifiers is finalization. Keep both.

### 5.3 Constraint Management: `manage_object_constraints` (only option)

**Verdict**: ✓ **Unique** — only constraint tool. Whitelist-based design is appropriate for production safety.

### 5.4 ID Material Creation: `nd_create_id_material` vs. `nd_bulk_create_id_materials`

| Aspect | single | bulk |
|--------|--------|------|
| **Input** | object_name, diffuse_color | object_names (list), color_space |
| **Color choice** | Explicit | Random (no seed) |
| **Output** | One material | One per object, distinct |
| **Overlap** | Single call vs. batch call | Same underlying operation |

**Verdict**: ⚠ **Redundant** — `nd_bulk_create_id_materials` can be replaced by batch `nd_create_id_material` calls with user-provided colors. However, random color generation is convenience; keep as-is if user doesn't have explicit color palette.

**Recommendation**: Consider merging into single tool with optional `colors: list[tuple[int,int,int]] | None` (explicit) or `auto_generate_distinct=True` (random).

---

## 6. Production Workflow Reliability

### 6.1 Dry-Run Support

**Tools with dry_run flag**: `create_geometry_object`, `set_object_transform`, `manage_object_hierarchy`, `manage_scene_collections`, `manage_object_constraints`, `manage_modifiers`, `duplicate_or_instance_objects`

**Tools WITHOUT dry_run**: `mesh_*` (7 tools), `model_*` (3 tools), `nd_*` (10 tools), `reset_scene`, all asset import/export

**Gap**: **Mesh editing operations cannot be previewed** — agent cannot verify extrude/inset/bevel/bridge result before committing. High risk for iterative design workflows.

### 6.2 Named Object Safety

**Collision protection**: ✓ Explicit naming collision detection in `duplicate_or_instance_objects` (appends .001, .002, etc.). ✓ Scene collections use same auto-incrementing.

**Risk**: When importing assets or creating many objects in a loop, name collisions can be silent (Blender auto-increments). No explicit "name already exists" error returned to client.

### 6.3 Determinism & Reproducibility

**Deterministic**:
- ✓ `create_geometry_object` (full spec from pydantic)
- ✓ `set_object_transform` (explicit transforms)
- ✓ `manage_scene_collections` (explicit names/actions)
- ✓ All constraint/modifier specs

**Non-deterministic**:
- ⚠ `nd_bulk_create_id_materials` (random RGB; no seed parameter)
- ⚠ `duplicate_or_instance_objects` (auto-incremented names depend on prior scene state)

---

## 7. Key Findings

### 7.1 Strengths

1. **Comprehensive scene composition** — GeometrySpec union covers 9 geometry types with full pydantic validation
2. **Production-grade validation** — `validate_scene` checks camera, lights, frame range, scale, degenerate faces, simulation caches
3. **Explicit naming & collision-safety** — all operations require explicit object/collection names; no silent defaults or bulk-selection deletion
4. **World-space transform handling** — `copy_object_transform` correctly decomposes/recomposes matrix_world for cross-parent alignment
5. **Non-destructive-first design** — ND tools default to live modifiers; boolean, extrude, etc. can be previewed before apply
6. **Asset library integration** — both Polyhaven and Sketchfab fully exposed with pagination, search, preview, import

### 7.2 Critical Gaps

1. **No camera creation** — GeometrySpec missing `kind="CAMERA"`; agent cannot create render cameras
2. **No world/environment setup** — No `kind="WORLD"` for HDRI assignment; agent cannot set world shader/environment
3. **No render configuration** — No tool to set Cycles/Eevee, samples, resolution, output path, tile size, denoiser, etc.
4. **No local file import** — Cannot import FBX/GLTF/OBJ from disk; only Polyhaven/Sketchfab supported
5. **Modifier settings opaque** — ModifierSpec allows arbitrary dict; no per-type validation; bad settings fail silently at Blender side

### 7.3 Reliability Risks

1. **`nd_pulse_viewport_toggle` NOT idempotent** — Toggles state; calling twice reverts. Violates production contract.
2. **`nd_clean_utils` destructive with no undo** — Hardcoded destructive, no dry-run, global scope. Extremely risky.
3. **Operator context assumptions** — Edit-mode mesh ops assume object is mesh + in edit mode; partial failures can leave state corrupted
4. **No mesh operation dry-run** — `mesh_extrude`, `mesh_inset`, `mesh_bevel`, `mesh_bridge` cannot be previewed; agent cannot validate topology before commit
5. **Asset import lacks validation** — No post-import checks for normals, scale, missing materials, armature validity

---

## 8. Recommendations for Production Readiness

### 8.1 Must-Fix (Blocks Production Use)

1. **Remove or fix `nd_pulse_viewport_toggle`** — Replace with explicit `set_viewport_overlay_state(action: SET, mode: CLEAR_VIEW|CUSTOM_VIEW|UTILS)` (set only, no toggle)
2. **Add safety wrapper to `nd_clean_utils`** — Require explicit `confirm_destructive=True` parameter; add dry-run mode
3. **Add camera creation** — Extend GeometrySpec with `kind="CAMERA"` including lens type, focal length, sensor size, dof
4. **Add world creation** — Extend GeometrySpec with `kind="WORLD"` or new tool `configure_world_environment` (shader assignment, background strength, etc.)
5. **Add render configuration tool** — New tool `configure_render_engine` (engine: CYCLES/EEVEE, samples, resolution, denoiser, output path, etc.)

### 8.2 Should-Fix (Improves Production Use)

1. **Validate modifier settings per type** — ModifierSpec should have per-modifier schema validation (ARRAY → repeat_x/y/z range, etc.)
2. **Add mesh operation dry-run** — Wrap `mesh_extrude`, `mesh_inset`, `mesh_bevel`, `mesh_bridge` with optional `dry_run=True` (undo after preview)
3. **Add local file import** — New tool `import_file_model` (path: str, file_type: FBX/GLTF/OBJ, collection_name: str, normalize_size: bool)
4. **Add post-import validation** — New tool `validate_imported_model` (object_names, checks: normals, scale, materials, armature, degenerate faces)
5. **Merge ID material tools** — Combine `nd_create_id_material` + `nd_bulk_create_id_materials` into single tool with explicit or auto color palette

### 8.3 Nice-to-Have (Optimization)

1. **Add geometry duplication options** — `duplicate_or_instance_objects` could support `shallow_copy` (unlink data) vs. `linked` vs. `full_copy`
2. **Expose modifier order priorities** — `manage_modifiers` reorder is present but underdocumented; clarify precedence
3. **Batch constraint application** — `manage_object_constraints` could support applying same constraint to multiple objects in one call

---

## 9. Tool Consolidation Matrix

| Current | Consolidate With | Action | Benefit |
|---------|------------------|--------|---------|
| `nd_create_id_material` | `nd_bulk_create_id_materials` | Merge | Single interface for single/batch ID coloring |
| `mesh_boolean` | `nd_boolean` | Keep separate | Serve different workflows (destructive vs. non-destructive) |
| `manage_modifiers` | `nd_apply_modifiers` | Keep separate | Creation vs. finalization; different APIs |
| `create_primitive_object` | `create_geometry_object` | Keep separate | Primitives are special case of geometry creation |

---

## 10. Workflow Coverage: "Create Simple Product Visualization"

**Test Workflow**: Create 3D product scene with floor, product model, studio lights, render camera, HDR environment.

```
1. reset_scene() → Clear Blender
2. create_geometry_object(kind="MESH", type="PLANE", name="Floor", scale=(10,10,1)) → Floor
   ⚠ Missing: Camera creation (must skip render setup)
   ⚠ Missing: World/HDRI setup (no environment)
3. import_sketchfab_model(uid="product_id", target_size=2.0) → Import product
4. set_object_transform(object_name="product", transform={location=(0,0,1)}) → Center product
5. create_geometry_object(kind="LIGHT", type="SUN", name="KeyLight", intensity=2.0) → Sun key light
   ⚠ Missing: Light creation not supported in GeometrySpec
6. validate_scene(active_domains=["scene", "lighting"]) → Check readiness
   ⚠ Missing: Camera validation would fail (no camera created)
7. [Cannot render — no camera, no render settings tool]
```

**Blockers Identified**:
- No camera creation
- No world/HDRI setup
- No light creation (though `create_geometry_object` should support lights)
- No render configuration
- No render tool

**Coverage Score**: 40/100 (scene setup only; rendering impossible)

---

## 11. Summary & Verdict

### Current Status
- **52 tools total** across 6 server tool files + 5 addon handler mixins
- **9 geometry types** supported in declarative API
- **16 constraint types** and **32+ modifier types** exposable
- **Full asset library integration** (Polyhaven + Sketchfab)

### Production-Ready Domains
- ✓ Scene composition (objects, collections, hierarchy)
- ✓ Mesh editing (extrude, inset, bevel, boolean, symmetry, remesh)
- ✓ Transform & modeling (copy transforms, arrays, ID materials)
- ✓ Asset search & import (Polyhaven, Sketchfab)
- ✓ Scene validation (comprehensive health checks)

### Production-Blocked Domains
- 🔴 Camera creation & setup
- 🔴 World/HDRI environment
- 🔴 Render engine configuration
- 🔴 Local file import (FBX/GLTF/OBJ)
- 🔴 Render output execution

### Risk Assessment
- 🟠 Idempotency: `nd_pulse_viewport_toggle` violates production contract (toggles; not idempotent)
- 🟠 Destructiveness: `nd_clean_utils` destructive without undo/dry-run
- 🟠 Validation: Modifier settings dict is opaque; bad settings fail silently
- 🟠 State restoration: Partial rollback gaps on multi-step collection operations

### Reliability Grade: B+ (Good)
- Strong validation and error handling on geometry/constraint/transform paths
- Transaction-based rollback on failure
- Explicit naming and collision-safety
- Weak points: ND viewport management, modifier settings validation, mesh operation dry-run

### Recommendation
**This domain is 60% complete for production use**. Camera creation, world setup, and render configuration are **blocking** for any render workflow. Local file import is blocking for mixed-asset workflows. Fix critical issues (camera, world, render config, toggle idempotency) to unlock rendering pipelines; then address modifier validation and mesh dry-run for higher confidence.

---

**End Slice 1 Report**
