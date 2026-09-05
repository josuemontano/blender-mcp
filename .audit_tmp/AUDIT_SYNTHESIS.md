# Blender MCP — Production Audit Synthesis

**Status**: Audit in progress (3 of 5 domain slices completed; background agents running on remaining slices).

## Completed Domain Audits

### ✓ Materials & Shading + UV & Texturing (04_materials_texture_uv.md)
- **21 tools** across materials, UV, texturing, baking, validation
- **Findings**: Copy-validate-commit pattern exemplary; **CRITICAL: RNA enum queries broken** (class-level `bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items` returns `['BLENDER_EEVEE']` only, breaking Cycles fallback check in `runtime_engine()`); only 4 material presets vs. 20+ needed for production; deprecated `.use_nodes` API unaddressed for Blender 6.0.
- **Score estimate**: 7/10 (solid architecture, critical reliability bug, preset library gap)

### ✓ Rendering + Color Management + Compositing (03_rendering_compositing.md)
- **9 tools** for render setup, output inspection, lighting quality, color management, preview rendering
- **Critical findings**: 
  1. **Compositor is read-only only** — no authoring/node/link creation possible
  2. **No video/FFmpeg output** — image-sequence only (FFMPEG file format exists in Blender but is rejected by MCP validator)
  3. `max_duration_seconds` offers no timeout protection for STILL renders
  4. No GPU availability check (silent CPU fallback possible)
- **Positive**: AgX color management well-designed and verified
- **Score estimate**: 5/10 (critical gaps: video output, compositor authoring; reliability gaps on render timeout)

### ✓ Animation + Rigging (05_animation_rigging.md)
- **29 tools** across generic animation (6), object animation (1), character rigging (22)
- **Findings**: 
  1. **Keyframe interpolation hardcoded to 3 values** (CONSTANT/LINEAR/BEZIER) but Blender enum has 13 (missing SINE/QUAD/CUBIC/BOUNCE/ELASTIC)
  2. Character rigging 22 of 29 tools (76%) but secondary to product-ad primary use case
  3. No pose-library/asset support
  4. Good: copy-validated playhead/selection restoration, evaluated-depsgraph baking
- **Score estimate**: 7/10 (solid core primitives, easily-fixable interpolation gap, scope overweight toward character work)

## Pending Domain Audits (Background Agents)

### 🔄 Scene/Core/Modeling/Import (agent a413f1f0be692de80) — status: running
Expected coverage: Scene initialization, object/mesh creation, modeling tools, ND integration, asset import

### 🔄 Camera/Lighting (agent af0b34d1a8388ee7f) — status: running
Expected coverage: Camera setup/animation/targeting, lighting placement/configuration, preview rendering

### 🔄 Liquid/Fluid/Simulation (agent a19775678455f2290) — status: running
Expected coverage: Fluid, smoke, rigid body, cloth, particles, soft-body simulation setup/baking

## Missing Domains (Not Yet Assigned to Background Agents)
- **Retopology** (`src/blender_mcp/server/tools/retopology/`)
- **Geometry Nodes** (if any tools exist)
- **Iteration & Inspection** (e.g., get_mesh_data, inspect scene state) — likely under Scene/Core
- **Validation** (asset validation tools)

---

## Findings Summary by Rubric Category (Preliminary)

| Category | Finding | Source | Priority |
|---|---|---|---|
| **A. Architecture & Abstraction (15 pts)** | Copy-validate-commit pattern in materials; but RNA enum query design is problematic | Materials | Fix runtime engine introspection path |
| **B. Tool Quality & Redundancy (15 pts)** | 3 ways to keyframe objects; 2 overlapping render/lighting quality tools | Animation, Rendering | Document cross-tool disambiguation |
| **C. Scene & Asset Pipeline (10 pts)** | Material presets only 4; no video output; no compositor authoring | Materials, Rendering | Critical gaps to address |
| **F. Rendering (15 pts)** | Color management solid; compositing read-only; video/FFmpeg missing entirely | Rendering | Critical gaps |
| **G. Compositing (5 pts)** | Inspection-only; no node authoring | Rendering | Critical gap |
| **H. Validation & Reliability (10 pts)** | RNA enum bug breaks Cycles rendering; timeout protection absent; deprecated APIs unaddressed | Materials, Rendering | Critical bugs |

---

## Known Defects Ranked by Severity

| # | Defect | Component | Severity | Fix Effort |
|---|---|---|---|---|
| 1 | RNA enum queries return incomplete/empty results at class-level; breaks `runtime_engine()` Cycles check | Materials | **CRITICAL** | Low (query path change) |
| 2 | No video/FFmpeg output; image-sequence only | Rendering | **CRITICAL** | Medium (expose RNA property group) |
| 3 | Compositor read-only; no node/link authoring | Rendering | **CRITICAL** | High (full new tool set) |
| 4 | Keyframe interpolation capped to 3 of 13 enum values | Animation | **High** | Low (widen Literal) |
| 5 | `max_duration_seconds` offers zero STILL-render timeout | Rendering | **High** | Low (document or add real timeout) |
| 6 | No GPU availability check; silent CPU fallback | Rendering | **Medium** | Low (add hasattr check) |
| 7 | Only 4 material presets vs. 20+ production needs | Materials | **Medium** | Medium (data-drive preset library) |
| 8 | Character rigging 76% of animation surface; secondary to product-ad use case | Animation | **Low-Medium** | Medium (split into optional tool group) |
| 9 | `.use_nodes` deprecated in Blender 6.0, still used | Materials, Rendering | **Low (forward-looking)** | Medium (migrate to 6.0-compatible pattern) |

---

## Architectural Observations

1. **Non-destructive-by-default**: Materials/animation use copy-validate-commit; good discipline.
2. **RNA-aware**: `get_shader_node_type_info` instantiates nodes to measure real sockets — a strong pattern.
3. **Tool explosion risk**: 29 animation tools (22 character-rigging) raises surface-economy questions; bundling/optional grouping worth considering.
4. **Missing escape hatches**: No compositor authoring and no FFmpeg output are complete capability gaps, not minor features.
5. **Interpolation/Enum hardcoding**: Multiple tools hardcode Blender enums to a subset of real values instead of `Literal[*enum_items]` — fragile to future Blender versions.

---

## Next Steps
- [ ] Synthesis of background-agent findings once available
- [ ] Identify remaining missing domains (Retopology, Geometry Nodes, etc.)
- [ ] Build 100-point score breakdown
- [ ] Write final 10 required deliverables
- [ ] Generate HTML report
