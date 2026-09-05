# Audit Slice: Rendering, Color Management, Compositing, Render Passes/AOVs, Video Production
## (Rubric §12 Cycles+Eevee, §13 Color Management, §14 Compositing, §15 Render Passes/AOVs, §16 Video Production)

Scope: `src/blender_mcp/server/tools/rendering.py`, `src/blender_mcp/server/tools/lighting/rendering.py`,
`src/blender_mcp/server/tools/texture/previews.py`, and their addon-side handlers
`src/blender_mcp/bundled/addon/handlers/rendering.py` and
`src/blender_mcp/bundled/addon/handlers/lighting/rendering.py` (+ `inspection.py`/`_shared.py` referenced by it).
Verified against a live `/opt/homebrew/bin/blender` 5.2.1 LTS runtime.

---

## 1. Tool inventory

| Tool | File:line | Purpose | Key params | Abstraction | Verdict |
|---|---|---|---|---|---|
| `inspect_render_setup` | `server/tools/rendering.py:190` | Read engine/output/color/camera/view-layers/passes/compositor state | `scene_name`, `graph_sections`, `limit/offset` | High (typed, paginated) | Solid |
| `configure_render_settings` | `server/tools/rendering.py:206` | Patch render/scene/image/cycles/eevee/motion-blur/film/output/metadata/multiview settings, no render | `scene_name`, `patch: RenderSettingsPatch` | High, deeply nested Pydantic model mirrored by an allowlist on the addon side | Solid, but see §5c overlap |
| `manage_view_layers` | `server/tools/rendering.py:216` | CREATE/PATCH/REMOVE a view layer incl. pass/Cryptomatte toggles | `action`, `patch: ViewLayerPatch`, `confirm_remove` | High | Solid |
| `render_scene` | `server/tools/rendering.py:245` | Render STILL/ANIMATION of the real scene to disk, metadata only | `filepath`, `mode`, `confirm_render`, `max_animation_frames`, `max_duration_seconds`, `verify_outputs/passes` | High | Solid API, but see §4b timeout gap and §6 no video output |
| `inspect_render_output` | `server/tools/rendering.py:315` | Return pixels of a previously rendered frame (file or in-memory Render Result) | `output_path`, `frame`, `max_size` | High | Functionally solid; see §5a error-type inconsistency |
| `configure_lighting_quality` | `server/tools/lighting/rendering.py:` (handler at `lighting/rendering.py:133`) | Patch Cycles/EEVEE sampling/GI/shadow quality, optionally via PREVIEW/BALANCED/FINAL presets | `target_engine`, `preset`, `cycles`, `eevee` | High, RNA-validated before mutation | Solid; overlaps `configure_render_settings` — see §5c |
| `configure_color_management` | `server/tools/lighting/rendering.py:93` (handler at `lighting/rendering.py:197`) | Patch view_transform/look/exposure/gamma, OCIO-validated | `view_transform: str`, `look`, `exposure`, `gamma` | High | Solid, confirmed against live AgX config — see §2 |
| `render_lighting_preview` | `server/tools/lighting/rendering.py:153` (handler at `lighting/rendering.py:238`) | Disposable staging-scene CYCLES/EEVEE/BOTH comparison render, inline PNGs | `target_engine`, `width/height`, `samples`, `confirm_long_render` | High, full state snapshot/restore incl. Render Result pixels | Solid |
| `render_pbr_material_preview` | `server/tools/texture/previews.py:18` | Isolated studio material preview (sphere/plane/cube), inline PNGs | `target_engine`, `geometry`, `resolution`, `confirm_cycles` | High | Solid; naming inconsistency — see §5b |

All nine tools are typed, validated Pydantic wrappers around narrow addon-side handler methods — no free-form `bpy` execution is exposed. This is a well-abstracted surface, not a thin passthrough.

---

## 2. Color management — verified against live Blender 5.2.1

Runtime check (`--background --python-expr`):

```
=== view_settings.view_transform enum + default ===
['NONE']              # bpy.props enum introspection returns no static items outside a GUI/OCIO-loaded context on this build
default view_transform: AgX
```

**AgX is confirmed as the live default `view_transform`** in Blender 5.2.1, matching `configure_color_management`'s docstring and `render_pbr_material_preview`'s fallback logic (`bundled/addon/handlers/texture/previews.py:117-124`, which explicitly probes `bl_rna.properties["view_transform"].enum_items` at runtime and falls back to `"AgX"` / `"AgX - Medium High Contrast"` / `"Medium High Contrast"` / `"None"` if present — a genuinely defensive, RNA-driven pattern, not a hardcoded guess).

`configure_color_management` (`lighting/rendering.py:197`) deliberately keeps `view_transform`/`look` as plain `str` (not a Pydantic `Literal`) and validates them by attempting the actual RNA assignment, catching `TypeError` and re-raising as a clear `ValueError` (`lighting/rendering.py:226-228`). This is the correct design given OCIO configs (and therefore valid view/look names) can differ across Blender builds/color-management profiles — a hardcoded enum would be wrong on some installs. `exposure` (±32 stops) and `gamma` ((0, 5]) are numerically bounded and validated with `finite_number`. **No gaps found in color management** — this is one of the better-executed areas of this slice.

One minor completeness gap: neither `configure_color_management` nor `_color_management_snapshot`/`inspect_render_setup` exposes **curves** (`scene.view_settings.curve_mapping`) or **use_curve_mapping**, which is part of Blender's color management stack (Filmic/AgX curve tweaks). Low severity — curves are a power-user feature — but worth noting as an incompleteness rather than a defect.

---

## 3. Compositing (rubric §14) — READ-ONLY, cannot author or edit a compositor graph

This is the most significant finding in this slice.

`inspect_render_setup` → `_compositor_info()` (`bundled/addon/handlers/rendering.py:185-244`) exposes **nodes, links, and dependencies** of the scene's compositor tree in a well-paginated, structured way (mirrors `list_scene_objects`-style `limit/offset/truncated/next_offset` pagination). It correctly resolves the Blender 5.x compositor tree via `scene.compositing_node_group` (new in 5.x) falling back to the removed `scene.node_tree` (`rendering.py:94,186`) — this dual-lookup is correct defensive RNA handling for the 4.x→5.x compositor API migration.

**But there is no tool anywhere in the codebase that creates, edits, or mutates a compositor node or link.** Grep confirms:

```
grep -rn "compositor\|compositing\|node_tree.*Compositor\|use_nodes" src/blender_mcp/
```
returns only: (a) the read-only `_render_info`/`_compositor_info` inspection code above, and (b) unrelated `material.use_nodes`/`world.use_nodes` hits in texture/lighting handlers (shader node trees, not compositing). There is no `configure_compositor`, no `add_compositor_node`, no glare/bloom/denoise/mask-node authoring tool, and no way to programmatically populate `scene.compositing_node_group` at all.

Runtime-confirmed consequence — enabling `scene.use_nodes = True` does **not** create a node group:

```
>>> scene.compositing_node_group  # before
None
>>> scene.use_nodes = True
DeprecationWarning: 'Scene.use_nodes' is expected to be removed in Blender 6.0
>>> scene.compositing_node_group  # after
None
```

So even if an agent wanted to compensate by driving `configure_render_settings`/raw RNA, there is no exposed path to construct a compositor tree at all (Blender normally creates one lazily when a user opens the Compositor workspace, or via `bpy.data.node_groups.new(type='CompositorNodeTree', ...)` assigned to `scene.compositing_node_group` — neither is wired up anywhere in this codebase). **Rubric §14 "Compositing" is inspection-only; there is no glare/bloom/denoise/mask node-graph configuration capability at all.** Given this repo scores 5 pts for "G. Compositing," this is a near-total gap on that axis.

Secondary finding: the addon still reads/writes `scene.use_nodes`, which the live 5.2.1 runtime reports as **deprecated, expected removed in Blender 6.0** (see warning above). This is forward-looking risk, not a current bug, but is worth flagging since CLAUDE.md targets "Blender 5.1+" without an explicit compatibility promise past 6.0.

---

## 4. Render passes / AOVs / Cryptomatte (rubric §15)

Contrary to an initial assumption that this might be entirely unsupported, **render passes and Cryptomatte ARE configurable**, via `manage_view_layers`'s `ViewLayerPatch` (`server/tools/rendering.py:148-178`) and the addon's `_VIEW_LAYER_PROPERTIES` allowlist (`bundled/addon/handlers/rendering.py:12-30`):

- Standard passes: `use_pass_combined`, `use_pass_z`, `use_pass_mist`, `use_pass_normal`, `use_pass_position`, `use_pass_vector`, `use_pass_uv`, `use_pass_object_index`, `use_pass_material_index`.
- Cryptomatte: `use_pass_cryptomatte_object/material/asset`, `pass_cryptomatte_depth` (even, 2–16).

**Gaps in this coverage:**

a. **No custom shader AOVs.** Blender's `ViewLayer.aovs` collection (arbitrary named AOVs driven by Shader-node "AOV Output" nodes) is entirely absent — `_VIEW_LAYER_PROPERTIES` has no `aovs` entry and there is no `add_aov`/`remove_aov`-style tool. An agent cannot register a custom AOV even though the render-pass infrastructure otherwise exists.
b. **No Cycles-specific light-contribution passes** (diffuse/glossy/transmission/volume direct+indirect+color, environment, emission, shadow, ambient-occlusion). Only the seven cross-engine passes above plus Cryptomatte are exposed; Cycles' much larger native pass set (`use_pass_diffuse_direct`, `use_pass_glossy_indirect`, `use_pass_emit`, `use_pass_environment`, `use_pass_ambient_occlusion`, `use_pass_shadow`, etc.) is not wired up anywhere.
c. **Verification is honest about a real Blender 5.2 API limitation.** `_render_pass_info()` (`bundled/addon/handlers/rendering.py:247-268`) correctly notes that Blender 5.2 removed `Image.layers` from the Python API, so it cannot enumerate rendered-pass names from the in-memory `Render Result` anymore; it falls back to reporting the *enabled view-layer pass configuration* instead of the *actually-rendered* pass list, and labels the result `"VIEW_LAYER_CONFIGURATION"` vs `"RENDER_RESULT"` so callers know which kind of evidence they're getting. This is a well-documented, honest degradation rather than a silent one — a good pattern, not a defect.

---

## 5. Redundancy / consistency issues

**a. Inconsistent error type in `inspect_render_output`.** `server/tools/rendering.py:373,382` raises bare `Exception(...)` (twice) instead of `ToolError` used everywhere else in this file and its siblings (`render_scene`, `manage_view_layers` all raise `ToolError`). This is the only tool in the three audited files that doesn't use the codebase's standard error type for user-facing tool failures.

**b. Naming inconsistency between the two preview tools.** `render_lighting_preview`'s `target_engine` enum is `CYCLES`/`EEVEE`/`BOTH` and internally expands `BOTH` → `["CYCLES", "EEVEE"]` (short key), while `render_pbr_material_preview` uses the type alias `TargetEngine` (from `texture/_shared.py`) whose `BOTH` expands to `["CYCLES", "BLENDER_EEVEE_NEXT"]` (full RNA engine id) — see `previews.py:46`. Two conceptually parallel "render this thing in both engines" tools use different vocabularies for the same concept, which is exactly the kind of surface inconsistency that costs an agent a wasted round-trip when composing calls (e.g. guessing which key belongs in `output_paths`).

**c. Real overlap between `configure_render_settings.cycles/eevee` and `configure_lighting_quality`.** Both tools can patch `scene.cycles.samples`, `use_adaptive_sampling`, `adaptive_threshold`, and `use_denoising` — `RenderSettingsPatch.CyclesPatch` (`rendering.py:123-133`) and `CYCLES_FIELDS` in `configure_lighting_quality` (`lighting/rendering.py:18-33`) both list these four fields, backed by two independent validation/rollback code paths (`_set_supported`+snapshot-reversal in `rendering.py` vs. `_validate_quality_owner`+`changes`-reversal in `lighting/rendering.py`). `configure_lighting_quality` additionally exposes `light_sampling_threshold`, clamp/bounce counts, `device`, and quality presets (PREVIEW/BALANCED/FINAL) that `configure_render_settings` cannot express, and the EEVEE side is even more skewed: `EeveePatch` in `rendering.py` only exposes `taa_samples`/`taa_render_samples`/`use_shadows`, while `configure_lighting_quality`'s `EEVEE_FIELD_MAP` additionally covers shadow pool/resolution/ray/step counts, raytracing method, fast GI, and volumetrics. An agent has no clear signal for *which* tool is authoritative for overlapping fields, and must already know to reach for `configure_lighting_quality` for anything beyond basic sampling. This isn't wrong, but it is genuinely confusing API surface, not just a documentation nit.

**d. `device: Literal["CPU", "GPU"]` in `CyclesLightingQuality` has no corresponding availability check.** See §7.

---

## 6. Video Production (rubric §16) — confirmed critical gap: image-sequence only, no movie/video output

Runtime-confirmed: Blender's own `render.image_settings.file_format` enum includes a full **movie** output format, `FFMPEG`, alongside still formats:

```
=== render.image_settings.file_format enum ===
['AVIF', 'JPEG', 'OPEN_EXR', 'PNG', 'WEBP', 'BMP', 'CINEON', 'DPX', 'IRIS',
 'JPEG2000', 'HDR', 'TARGA', 'TARGA_RAW', 'TIFF', 'OPEN_EXR_MULTILAYER', 'FFMPEG']
=== has ffmpeg settings? ===
True
['format', 'codec', 'video_bitrate', 'minrate', 'maxrate', 'muxrate', 'gopsize',
 'max_b_frames', 'use_max_b_frames', 'buffersize', 'packetsize',
 'constant_rate_factor', 'custom_constant_rate_factor', 'ffmpeg_preset',
 'ffmpeg_prores_profile', 'use_autosplit', 'use_lossless_output',
 'audio_codec', 'audio_bitrate', 'audio_volume', 'audio_mixrate', 'audio_channels']
```

`scene.render.ffmpeg` is a full RNA property group (container format, codec, bitrate/rate-control, GOP size, B-frames, audio codec/bitrate/mixrate/channels) — Blender's complete encoded-video pipeline.

**None of this is reachable from the MCP surface.** `RenderSettingsPatch.image_format` (`server/tools/rendering.py:39`) and `OutputPatch.image_format` (`rendering.py:87`) are both `Literal["PNG", "JPEG", "OPEN_EXR", ...]` — `"FFMPEG"` is not one of the allowed values, and the addon's own allowlist validation (`_validate_render_patch`'s `allowed_values["image_format"]`, `bundled/addon/handlers/rendering.py:329`) independently excludes it too, so this isn't just a client-side typing gap that a raw dict could route around — the server actively rejects it. There is no Pydantic model, no handler code, and no grep hit anywhere in `src/blender_mcp/` for `ffmpeg`, `codec`, or `bitrate`. `render_scene`'s `ANIMATION` mode (the only multi-frame render path) can therefore only ever produce a numbered image sequence on disk (confirmed by `_render_pass_info`/`frame_path` logic in `bundled/addon/handlers/rendering.py:588-625`, which always calls `scene.render.frame_path(frame=...)` per frame) — **there is no way to make this codebase emit a single encoded video file (.mp4/.mov/.webm/etc.) end to end.** For a rubric category explicitly named "Video Production," this is a complete, verified gap, not a matter of degree.

`motion_blur` (rubric-adjacent) is, by contrast, fully supported: `MotionBlurPatch` (`rendering.py:64-70`, `enabled`/`shutter`/`position`) round-trips correctly to `render.use_motion_blur`/`motion_blur_shutter`/`motion_blur_position` via the `_set_supported` allowlist mapping in `bundled/addon/handlers/rendering.py:392-400`, and is included in both the inspect and configure paths. Motion blur is not part of the video-output gap above.

---

## 7. Reliability assessment

**a. Output verification is real, not assumed.** `render_scene` checks `os.path.isfile(frame_output)` after every `bpy.ops.render.render()` call and raises `RuntimeError` if the operator reports `FINISHED` but the file is missing (`bundled/addon/handlers/rendering.py:614-618`) — this correctly distinguishes "Blender said it worked" from "the file actually exists," which CLAUDE.md's "Validate operator results" guidance specifically calls for. `render_lighting_preview` goes further, also checking `os.path.getsize(...) <= 0` (`lighting/rendering.py:315-316`) to catch a zero-byte write.

**b. `max_duration_seconds` on `render_scene` is a per-frame boundary check, not a real render timeout — and provides ZERO protection for `mode="STILL"`.** The check (`bundled/addon/handlers/rendering.py:595-597`) runs only at the *top* of the per-frame loop, before calling `bpy.ops.render.render(...)`, which is a **blocking, synchronous call** with no way to interrupt mid-render. For `mode="STILL"` there is exactly one frame; the timeout check evaluates once (at ~0 elapsed seconds, so it always passes) and then the single blocking render call has no timeout enforcement whatsoever — a pathologically slow single-frame Cycles render (e.g., an agent-set sample count that's too high, or a scene with an expensive volumetric/SSS setup) cannot be bounded by `max_duration_seconds` at all despite the parameter accepting values up to 7 days and appearing in the tool's public contract as a safety control. For `mode="ANIMATION"`, the same gap means only *whole extra frames* are prevented — an in-progress frame that blows past the budget still runs to completion before the next check fires. This materially undercuts the docstring/parameter's implied guarantee and should be flagged as the primary reliability gap in this slice.

**c. No GPU-availability detection or fallback for Cycles `device`.** `CyclesLightingQuality.device: Literal["CPU", "GPU"]` (`lighting/rendering.py:34`, exposed through `configure_lighting_quality`) is patched directly via `setattr(scene.cycles, "device", "GPU")` with no corresponding check of `bpy.context.preferences.addons["cycles"].preferences.compute_device_type` or `get_devices()`. Grep confirms no `compute_device`, `CUDA`, `OPTIX`, or `get_devices` reference anywhere in `src/blender_mcp/`. In real Blender, setting `scene.cycles.device = "GPU"` when no compute device is actually enabled in user Preferences does not raise — Cycles silently renders on CPU instead. An agent (or user) that requests GPU rendering via this tool has no way to detect that it silently fell back to CPU; the response envelope's `before`/`after` snapshot (`_quality_snapshot`) will happily report `"device": "GPU"` as the *setting value* even though the actual render device is CPU. This is a real, verifiable reliability gap, not a hypothetical one.

**d. `configure_lighting_quality`'s "runtime RNA checks" docstring claim is substantiated.** `_validate_quality_owner()` (`lighting/rendering.py:65-76`) does preflight every field with `hasattr()` and, for enum fields, checks the value against `owner.bl_rna.properties[field].enum_items` before any assignment — and `configure_render_settings`'s `_set_supported()` (`bundled/addon/handlers/rendering.py:287-292`) does the equivalent `hasattr` preflight for its own nested patches. Both correctly reject unsupported settings up front rather than silently no-op'ing, and both roll back partial changes via before/after value snapshots on exception (`_set_properties`/`_restore_properties`). This is good, verified reliability engineering — not a gap.

**e. `render_lighting_preview`'s state restoration is unusually thorough**, including restoring the in-memory `Render Result` datablock's actual pixels (not just render settings) via `_snapshot_render_result()`/`_restore_render_result()` (`lighting/rendering.py:93-127`), with an explicit size cap (`MAX_RENDER_RESULT_FLOATS`) that raises rather than attempting an unbounded pixel-array copy. This is a genuinely careful design most similar tools in other codebases skip.

---

## 8. Summary of severity-ranked findings

| # | Finding | Severity | Section |
|---|---|---|---|
| 1 | No video/movie (FFmpeg) output anywhere — image-sequence only, verified against live `FFMPEG` file_format + full `ffmpeg` RNA property group that exists in Blender but is entirely unreachable from this MCP server | **Critical** | §6 |
| 2 | Compositor is read-only inspection only — no node/link authoring tool, no way to even create a compositor node group | **Critical** | §3 |
| 3 | `max_duration_seconds` gives zero timeout protection for STILL renders and only whole-frame granularity for ANIMATION | **High** | §7b |
| 4 | GPU `device="GPU"` has no availability check/fallback signal — silent CPU fallback is possible and undetectable via the response | **Medium** | §7c |
| 5 | No custom shader AOVs, no Cycles-native light passes (diffuse/glossy/emission/AO/etc.) beyond the 7 cross-engine passes + Cryptomatte | **Medium** | §4a-b |
| 6 | Overlapping Cycles/EEVEE quality fields split across two differently-validated tools (`configure_render_settings` vs `configure_lighting_quality`) | **Low-Medium** | §5c |
| 7 | `inspect_render_output` raises bare `Exception` instead of `ToolError` | **Low** | §5a |
| 8 | Preview-tool engine-key naming inconsistency (`"EEVEE"` vs `"BLENDER_EEVEE_NEXT"`) between `render_lighting_preview` and `render_pbr_material_preview` | **Low** | §5b |
| 9 | `scene.use_nodes` is deprecated, expected removed in Blender 6.0, still read/written by the addon | **Low (forward-looking)** | §3 |
| 10 | Color management (AgX default, view_transform/look/exposure/gamma) is well-designed and runtime-verified correct — not a gap | Positive finding | §2 |

Out of scope note: camera/lighting object placement, world/environment setup, and PBR material authoring itself were not assessed here (owned by other audit slices) — only the render/color/compositing/pass/video *configuration and execution* surface was evaluated.
