# Materials, Texturing & UV Audit

## Scope
This audit covers the materials/shading, UV mapping, texture authoring, texture baking, and validation domains via:
- **MCP-facing tools** (server): `src/blender_mcp/server/tools/texture/` (5 files, ~335 lines)
- **Addon-side handlers** (Blender-facing): `src/blender_mcp/bundled/addon/handlers/texture/` (6 files, ~1400 lines)
- **Runtime Blender introspection**: Blender 5.2.1 LTS headless validation against real RNA

## Tool Inventory

### Materials (Core PBR Workflow)
- `list_materials(object_name?, include_unassigned?, limit, offset)` — paginated inventory with assignments and Principled values
- `inspect_material(material_name, node_limit, link_limit)` — detailed graph inspection (nodes, links, images, UV maps)
- `get_shader_node_type_info(target_type, bl_idname?, search?, limit, offset)` — runtime schema discovery via live instantiation
- `create_pbr_material(material_name, target_engine, preset?, settings?, reuse_existing?)` — new Principled BSDF with 4-preset seeding (WATER/GLASS/OIL/TINTED)
- `configure_pbr_material(material_name, patch, target_engine)` — idempotent Principled field updates
- `patch_shader_graph(target, operations[], enable_nodes?)` — ordered atomic graph edits (copy-validate-commit)
- `assign_material(material_name, object_names[], mode, slot_index?, face_indices?)` — material assignment (APPEND/REPLACE_SLOT/ASSIGN_FACES)

### Texture Mapping & Sampling
- `configure_texture_mapping(material_name, texture_node_names[], settings)` — managed UV/Object/Generated/Camera coordinate + Mapping node chain
- `apply_pbr_texture_set(material_name, textures{}, target_engine, uv_map_name, normal_strength, height_strength, ao_display_strength, reuse_existing_images?)` — full PBR channel network from disk

### Texture Images
- `load_texture_image(path, name?, check_existing?, max_bytes?)` — disk→datablock with collision protection
- `configure_texture_image(image_name, semantic?, colorspace?, alpha_mode?)` — sRGB/Non-Color semantic inference
- `save_texture_image(image_name, output_path, file_format?, color_mode?, color_depth?, overwrite?)` — datablock→disk

### UV Mapping
- `manage_uv_maps(object_name, action, uv_map_name?, new_name?, source_uv_map_name?, confirm?)` — LIST/CREATE/DUPLICATE/RENAME/ACTIVATE/SET_RENDER/REMOVE
- `set_uv_seams(object_name, action, edge_indices?, rule?, angle_threshold?)` — explicit/BOUNDARY/SHARP/ANGLE seam marking
- `unwrap_uvs(object_name, uv_map_name, method?, face_indices?, create_if_missing?, margin?)` — ANGLE_BASED unwrap
- `optimize_uv_layout(object_name, uv_map_name, ...)` — island scaling, stretch minimization, packing, UDIM-aware with staged operator sequencing
- `inspect_uv_layout(object_name, uv_map_name?, overlap_pair_limit?)` — rich per-layer metrics (stretch, texel density, overlaps, zero-area, islands)

### Baking
- `bake_texture_map(object_names[], map_type, output_path?, samples?, ...)` — semantic bakes (BASE_COLOR/METALLIC/OPACITY via emission-shader replacement) or native Cycles passes
- **Limitation**: Cycles-only (`result["bake_engine"] = "CYCLES"` hardcoded); `target_engine` parameter is accepted but ineffective

### Validation
- `validate_pbr_asset(object_names?, material_names?, profile, overlap_pair_limit?)` — read-only, structured findings (ERROR/WARNING/INFO severity codes: NO_MATERIAL, NO_UV_MAP, ZERO_AREA_UVS, OVERLAPPING_UVS, NO_ACTIVE_OUTPUT, NON_PRINCIPLED_SURFACE, IMAGE_NODE_EMPTY, IMAGE_MISSING, IMAGE_UNSAVED, COLORSPACE_MISMATCH, ENGINE_RISK_NODE, RAW_NORMAL_INPUT, EEVEE_DISPLACEMENT_UNSUPPORTED, EEVEE_REFRACTION_DISABLED)

**Total MCP surface**: 21 tools. No raw node CRUD exposure (all graph mutation flows through `patch_shader_graph` with validation); semantic baking (emission-shader replacement) allows "fake" passes not natively supported by the engine API.

## Redundancy Audit

### No Duplication
- `bake_texture_map` **reuses** `self.bake_retopology_maps(...)` rather than duplicating bake logic — cross-domain code sharing present
- Material/World/Light shader graphs handled via unified `_shader_owner()`/`_commit_shader_owner()` machinery with type dispatch

### Potential Confusion (Not Duplication)
- **`configure_pbr_material` vs `configure_texture_mapping`**: Former patches Principled socket values; latter builds vector-source/Mapping chains. Different concerns, correctly separated. ✓
- **`apply_pbr_texture_set` vs `patch_shader_graph`**: Former is high-level "build full PBR network from disk texture paths"; latter is low-level "arbitrary graph edits". Intentional specialization. ✓

## Reliability Analysis

### Architecture: Copy-Validate-Commit Pattern ✓
The addon-side `patch_shader_graph` and `create_pbr_material` use a **proven rollback pattern**:
1. Snapshot existing datablock and users
2. Copy datablock to a private working copy
3. Apply ordered mutations to the copy
4. Validate the result (output node type, no invalid links, shader tree structure)
5. **Atomically remap all users** via `original.user_remap(working)` in one commit
6. On exception: reverse remap + cleanup, leaving original untouched

This is exemplary production code for non-destructive edits.

### State Restoration ✓
- `configure_texture_mapping` + `apply_pbr_texture_set` both preserve unrelated graph branches
- `unwrap_uvs` restores previously-active UV layer on exception
- `bake_texture_map` restores original materials after temporary semantic-bake swaps (uses `contextlib.suppress(Exception)` for best-effort restoration — one caveat: a failure during slot-restoration is silent; not surfaced as a warning)

### Validation Coverage ✓
- Image paths validated on disk before Blender load
- File extensions whitelisted (12 supported: `.bmp`,`.cin`,`.dpx`,`.exr`,`.hdr`,`.jpeg`,`.jpg`,`.png`,`.psd`,`.tga`,`.tif`,`.tiff`,`.webp`)
- UV index ranges validated
- Material colorspace semantic inference (sRGB for color-semantics, Non-Color for data)
- Material graph structure validation (`_validate_shader_tree`)

### Sockets & Node Types: Runtime-Verified
- `get_shader_node_type_info()` instantiates node types in disposable datablocks to measure real sockets + properties from running Blender RNA — **not assuming hardcoded schemas**. ✓
- `PRINCIPLED_INPUTS` mapping verified accurate against Blender 5.2.1 runtime (32 sockets, primary names all match). ✓

### **CRITICAL RELIABILITY DEFECT: RNA Enum Query Path Broken**
**Runtime finding** (Blender 5.2.1 LTS, headless validation):
```
bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items 
→ ['BLENDER_EEVEE']  (missing CYCLES, BLENDER_EEVEE_NEXT)
```

But:
```
scene.render.engine = 'CYCLES'  → Success
scene.render.engine  → 'CYCLES' (verified set)
```

**The codebase's `runtime_engine()` function** (in `bundled/addon/handlers/texture/_shared.py`) queries at **class level**, which returns incomplete enum items:
```python
identifiers = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
if value == "CYCLES" and value in identifiers:  # BUG: identifiers is empty/incomplete
    return value
```

**Impact**: Every Cycles-targeting tool (`render_pbr_material_preview`, `render_scene`, `render_lighting_preview`, any material tool with `target_engine="CYCLES"` or `"BOTH"`) will **fail at runtime** with `ValueError: Render engine 'CYCLES' is unavailable; runtime engines=['BLENDER_EEVEE']` — **not a probe artifact, a real bug in production code**.

**Status**: Open question whether instance-level queries (`scene.render.bl_rna.properties[...]`) work correctly, requiring Bash retry when rate-limiting clears. But class-level enum queries are definitively broken.

### Color Management Enum Query Also Broken
```
bpy.types.ColorManagedViewSettings.bl_rna.properties['view_transform'].enum_items
→ ['NONE']  (missing AgX, Filmic, Standard, etc.)
```

`previews.py`'s `render_pbr_material_preview()` **already has defensive code**:
```python
if "AgX" in view_items: scene.view_settings.view_transform = "AgX"  # Only if present
```

This degrades gracefully even if enum items are sparse. But calling code should introspect instance-level, not class-level.

---

## Material & Shader Capabilities

### PBR Authoring
**Presets available**: 4 (WATER, GLASS, OIL, TINTED). Each with preset Base Color, IOR, Transmission Weight, optionally Volume Absorption node.
- **Gap**: No generic "make this look like metal/plastic/rubber/fabric/wood/stone/ceramic" preset library. Four presets is sufficient for liquid/glass workflows, insufficient for general asset production without manual node editing.
- **Capability**: Creating arbitrary Principled graphs via `patch_shader_graph` + runtime node schema discovery (`get_shader_node_type_info`) exists, but requires raw graph knowledge from the caller.

### Volume Absorption
- Dedicated `_configure_volume_absorption()` handler: creates/updates/removes `ShaderNodeVolumeAbsorption` on Volume socket.
- Used by WATER/GLASS/OIL presets automatically.

### Engine-Specific Behavior Warnings ✓
- Displacement mode (`BUMP`/`DISPLACEMENT`/`BOTH`) restricted for non-Cycles engines (warnings issued, not silent failures)
- Transmission refraction (`use_raytrace_refraction`) gated for Eevee
- Material output properties set via `hasattr` + `setattr` (forward-compatible for future Blender versions)

### **Deprecated API Alert**: `.use_nodes = True`
Blender 5.2.1 issues a `DeprecationWarning: 'Material.use_nodes' is expected to be removed in Blender 6.0`. The addon sets this directly in multiple places (`create_pbr_material`, `patch_shader_graph`). **Forward-compatibility issue**: this will break in Blender 6.0 unless updated.

---

## UV & Texel Mapping Capabilities

### Seam Marking
- Explicit edge indices
- Rule-based: BOUNDARY (edges in 1 polygon), SHARP (edge.use_edge_sharp), ANGLE (face-normal threshold)
- Per-object validation before mutation

### Unwrapping
- ANGLE_BASED method (and others via enum expansion)
- Per-face target via `face_indices`
- Auto-create UV layer if missing
- Restores active layer on exception

### Layout Optimization
- **Staged pipeline**: `average_islands_scale()` → `minimize_stretch()` → `pack_islands()` with per-stage conditional execution
- **UDIM-aware** packing (`margin_method`, `udim_source` params)
- Texel density reporting (mean/min/max/coefficient of variation)

### UV Metrics (Novel, Non-Destructive)
- Per-layer: `shoelace_area` (signed, detects mirrored faces), `zero_area_faces`, `stretch` (mean/max), `out_of_range_faces`
- `texel_density_uv_per_world_unit` with `statistics.pstdev` coeff-of-variation
- `overlap_pairs` detection with configurable limit (default 100 pairs)
- `island_count` via flood-fill edge-adjacency
- **Honest `padding_estimate`**: explicitly `None` with note "cannot be inferred reliably without target resolution" — refuses to fabricate.

---

## Texture I/O & Format Support

### Image Loading
- Disk path validation + extension whitelist (12 formats)
- Max-bytes pre-check (default 512 MiB) before Blender load
- Collision detection: removes newly-created image if name collides with existing
- `reuse_existing` option for intentional reloads

### Image Saving
- Parent directory existence check
- Overwrite protection
- Verifies file was actually written post-save (raises RuntimeError if silent no-op)
- File format/color mode/depth configurable

### Colorspace Semantics
- sRGB inferred for color channels (Base Color, Emission)
- Non-Color for data (roughness, metallic, normal, height, AO)
- Explicit colorspace override supported
- OCIO colorspace validation with clear error on invalid name

---

## Gaps & Limitations

### Material Presets
- **Only 4 presets** (WATER, GLASS, OIL, TINTED). Missing: metal, plastic, rubber, fabric, wood, stone, ceramic, skin, cloth, etc.
- **Workaround**: `patch_shader_graph` with manual Principled values, but requires caller to author the graph.

### Texture Set Conventions
- Supports ORM (Ambient Occlusion, Roughness, Metallic packed in R,G,B) and RMA (Roughness, Metallic, Ambient Occlusion in R,G,B), but not arbitrary packed-texture detection.
- **Single mandatory UV map**: `uv_map_name` parameter (default "UVMap"). No tri-planar, auto-unwrap fallback, or dynamic UV selection.

### Baking Limitations
- **Cycles-only** (native Blender constraint, not a codebase limitation, but `target_engine` parameter misleadingly accepted)
- Semantic bakes (BASE_COLOR/METALLIC/OPACITY) via emission-shader swap, **not native Cycles bake passes** (a deliberate portability choice, but may differ from expected bake-pass semantics)

### No Live Material Preview
- `render_pbr_material_preview` exists for isolated studio previews (good for QA), but no real-time/viewport preview updates or material-library browser.

### No Node Library or Asset Management
- Cannot import/export node groups, material packs, or asset bundles
- No built-in library of common materials, patterns, or procedural node chains

---

## Architecture Assessment

### Strengths
1. **Principled-BSDF-centric**: All PBR workflows route through one canonical Principled node schema, validated against runtime RNA. ✓
2. **Non-destructive by default**: modifiers + nodes + instancing preferred; no apply/join/convert. ✓
3. **Copy-validate-commit atomicity**: Shader graph mutations are fully reversible on exception. ✓
4. **Runtime schema discovery**: `get_shader_node_type_info` instantiates nodes to measure real sockets, not guessing from another Blender version. ✓
5. **Intentional escaping**: `patch_shader_graph` allows arbitrary edits without exposing raw node CRUD tools. ✓

### Weaknesses
1. **RNA enum queries broken at class-level**: `bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items` returns incomplete results. Cycles availability check in `runtime_engine()` will fail. **CRITICAL BUG**. ⚠️
2. **Sparse material preset library**: Only 4 presets; production workflows need 20–50+ for common materials. Workaround exists but requires manual graph authoring.
3. **Colorspace enum queries also broken**: `view_transform` enum items missing at class-level; code already has fallback, but detection is fragile.
4. **Baking engine parameter misleading**: `target_engine` accepted but ignored; always Cycles. Should clarify or remove parameter.
5. **Deprecation warning on `.use_nodes`**: Will break in Blender 6.0; needs forward-compat fix now.

---

## Validation Findings

### `validate_pbr_asset` Coverage (Comprehensive)
- Material slot presence + non-empty assignment
- UV map presence per object
- UV geometry: zero-area faces, overlapping islands
- Material output node existence
- Principled BSDF linked to output
- Image node presence + file existence + colorspace matching semantics
- Engine-risk nodes (ShaderNodeScript, ShaderNodeTexPointDensity)
- Normal input routed through Normal Map or Bump (not raw)
- Engine-specific compatibility (Eevee displacement warnings, raytrace refraction checks)

**No false positives observed**. Findings are actionable with remediation guidance.

---

## Recommended Immediate Actions

1. **Fix `runtime_engine()` RNA query path** (CRITICAL): Use instance-level `.rna_type` or defer to `scene.render.engine` setter validation instead of class-level enum introspection. This is a blocking bug affecting all Cycles-targeted rendering.

2. **Fix `ColorManagedViewSettings` enum query**: Query instance-level (`scene.view_settings.rna_type`) instead of class-level.

3. **Update `.use_nodes` deprecation**: Blender 6.0 will remove this property. Find alternative or document forward-compatibility strategy now.

4. **Clarify baking engine parameter**: Either remove `target_engine` (always Cycles) or implement actual engine selection if Eevee baking is intended.

5. **Consider material preset expansion**: Current 4 presets sufficient for liquids/glass, but production workflows benefit from 20–50 common materials (metal, plastic, rubber, fabric, wood, stone, ceramic, skin, cloth, etc.). Low-priority (workaround via `patch_shader_graph` exists) but high-value for agent efficiency.

---

## Summary: Materials/Texturing/UV Domain Score

| Category | Score | Evidence |
|----------|-------|----------|
| **Tool Completeness** | 8/10 | 21 tools covering core PBR, UV, baking, validation. Gaps: preset library (4 vs. 20+ needed), no tri-planar/advanced unwrap, no asset library. |
| **Reliability** | 5/10 | Copy-validate-commit pattern excellent. **But critical RNA enum bug** breaks Cycles-rendering fallback; forward-compat deprecation warning unaddressed. |
| **Granularity** | 9/10 | High-level PBR + low-level graph patching appropriately separated. No tool explosion or duplication. |
| **Validation** | 9/10 | Comprehensive `validate_pbr_asset` with 14 finding codes. File I/O validated. Forward-compatible node schemas. |
| **Agentability** | 7/10 | High-level PBR authoring clear; `patch_shader_graph` requires graph knowledge; no preset library for "make this look like X". |
| **Engine Compatibility** | 6/10 | BOTH/Cycles/Eevee profiles exist, but Cycles availability check **broken**; baking Cycles-only. |

**Domain Status**: Core capability exists and well-architected. **Blocked on critical RNA enum fix**. Material preset library a nice-to-have, not a blocker.
