# Production-Grade PBR Texturing Tool Plan for Cycles and Eevee

## Recommended production tool surface

Implement 27 public tools in three waves, optimized for materials that render
predictably in Blender's Cycles and Eevee engines. Shared Principled BSDF
workflows are the default; engine-specific behavior must be explicit and
validated. The existing retopology UV, attribute-transfer, cage, and baking
handlers should be generalized and reused rather than duplicated.

## Cycles and Eevee compatibility contract

- Material creation, configuration, texture-set application, preview, and
  validation accept `target_engine`: `BOTH`, `CYCLES`, or
  `BLENDER_EEVEE_NEXT`. `BOTH` is the production default.
- Validate render-engine identifiers against runtime RNA because Blender may
  rename engine enum values. Public schemas retain the stable semantic names
  `CYCLES` and `EEVEE`.
- For `BOTH`, construct one conventional Principled graph using features that
  have equivalent behavior in both engines. Report compromises rather than
  silently creating engine-specific graph branches.
- Cycles is the authoritative engine for texture baking and true material
  displacement. Eevee targets use Normal Map and Bump nodes; true displacement
  is reported as unsupported rather than approximated silently.
- Eevee-specific transparency, refraction, thickness, backface, and surface
  rendering requirements are material configuration, not implicit scene-wide
  mutations. When a scene render option is required, report it as a validation
  finding or change it only through an explicit rendering tool.
- Preview and validation results identify the engine actually used, device,
  sample count, color management, and any unsupported or approximated feature.
- Texture colorspace, UV, normal orientation, and image-storage contracts are
  shared across engines.

## 1. Material and shader tools

### 1. `list_materials` — P0

**Description:** Paginated inventory of materials, assignments, shader type,
image count, and basic Principled properties.

**Implementation:** Read `bpy.data.materials`, object material slots,
`Material.use_nodes`, and `Material.node_tree`. Support `object_name`,
`include_unassigned`, `limit`, and `offset`. Do not serialize entire node graphs
here.

### 2. `inspect_material` — P0

**Description:** Detailed, read-only inspection of one material and its
effective shader graph.

**Implementation:** Return nodes, links, socket defaults, image dependencies,
UV-map usage, mapping nodes, output connections, blend/surface settings, and
unsupported node types. Identify nodes using `bl_idname` and socket identifiers
rather than display labels where possible. Bound and paginate large graphs.

### 3. `create_pbr_material` — P0

**Description:** Create a clean Principled BSDF material without assigning it
implicitly.

**Implementation:** Use `bpy.data.materials.new`, enable nodes, ensure exactly
one `ShaderNodeBsdfPrincipled` connected to `ShaderNodeOutputMaterial`, and
initialize explicit PBR defaults. Reject name collisions unless
`reuse_existing=True`; never delete an existing material.

### 4. `configure_pbr_material` — P0

**Description:** Patch Principled parameters and material-level rendering
behavior for Cycles, Eevee, or their compatible intersection.

**Implementation:** Accept only supplied fields: base color, metallic,
roughness, IOR, transmission, coat, sheen, emission, alpha, normal strength,
and displacement mode. Validate finite ranges through RNA before commit. Update
the shader feeding the active Material Output rather than whichever Principled
node appears first. With `target_engine=BOTH`, use bump/normal mapping and warn
when a requested feature does not have equivalent Cycles/Eevee behavior. For
Cycles-only materials, allow a Displacement node connected to Material Output.
For Eevee, configure the Blender 5.1 material surface-rendering, transparency,
refraction, thickness, backface, and shadow behavior through verified RNA
properties and report any required scene settings separately.

### 5. `assign_material` — P0

**Description:** Assign or replace a material slot on explicitly named objects
or faces.

**Implementation:** Prevalidate every object, material, slot, and face index.
Use `obj.data.materials` and `mesh.polygons[i].material_index`; support `APPEND`,
`REPLACE_SLOT`, and `ASSIGN_FACES`. Never clear all slots implicitly. Restore
active object, selection, and mode.

### 6. `patch_shader_graph` — P1

**Description:** Apply one validated, transactional graph patch instead of
exposing many fragile node CRUD tools.

**Implementation:** Support bounded `add_nodes`, `update_nodes`, `remove_nodes`,
`add_links`, and `remove_links`. Prevalidate node types, unique IDs, socket
compatibility, properties, and the complete resulting graph before mutation.
Tag MCP-managed nodes with custom properties and roll back all changes on
failure. This replaces separate `add_shader_node`, `connect_shader_nodes`,
`delete_shader_node`, and similar calls.

### 7. `configure_texture_mapping` — P0

**Description:** Configure UV, Object, Generated, camera, or box/triplanar
mapping for one or more texture nodes.

**Implementation:** Create or reuse managed `ShaderNodeUVMap`,
`ShaderNodeTexCoord`, and `ShaderNodeMapping` nodes. Configure Image Texture
`projection`, `projection_blend`, `interpolation`, and `extension`. Accept an
explicit UV map and vector transform; do not rewrite unrelated graph branches.

## 2. Image and texture-set tools

### 8. `list_texture_images` — P0

**Description:** Paginated inventory of Blender image datablocks and their
material usage.

**Implementation:** Return dimensions, channels, bit depth where available,
source, colorspace, alpha mode, filepath, packed state, dirty state, UDIM tiles,
users, missing-file status, and estimated memory. Use `bpy.data.images` and
`Image` properties.

### 9. `load_texture_image` — P0

**Description:** Load a validated local image as a reusable Blender datablock.

**Implementation:** Resolve and authorize an explicit path, check extension and
size limits, then call `bpy.data.images.load(path, check_existing=True)`. Return
whether the image was loaded or reused. File decoding may run outside the
Blender mutation phase, but datablock creation stays on the main thread.

### 10. `configure_texture_image` — P0

**Description:** Set image interpretation without changing its pixels.

**Implementation:** Configure `image.colorspace_settings.name`,
`image.alpha_mode`, and supported image-source properties. Default semantic
color maps to sRGB and data maps—normal, roughness, metallic, AO, height—to
Non-Color. Require explicit overrides when the requested colorspace is
unavailable in the active OCIO configuration.

### 11. `manage_texture_storage` — P1

**Description:** Explicitly reload, repath, pack, or unpack an image.

**Implementation:** Use `Image.reload()`, `Image.pack()`, and supported
unpack/repath APIs. Treat repathing and unpacking as filesystem changes; require
explicit paths and overwrite confirmation. Report dirty or unsaved pixel data
before reload.

### 12. `apply_pbr_texture_set` — P0

**Description:** Build a complete Cycles/Eevee Principled material from a local
PBR texture set.

**Implementation:** Accept an explicit channel-to-file mapping, with optional
deterministic filename discovery. Recognize base color, metallic, roughness,
glossiness, normal OpenGL/DirectX, height, displacement, AO, opacity, emission,
and packed ORM/RMA inputs. Create managed image, Normal Map, Bump/Displacement,
Separate Color, and mapping nodes. Error on ambiguous matches. Default to a
dual-engine graph: tangent-space normals and bump feed the Principled normal
input, while true displacement is enabled only for `target_engine=CYCLES`.
Never silently multiply AO into base color; expose it as an Eevee-oriented
display policy with an explicit strength because the Principled shader has no
AO socket. This should replace the Poly Haven-specific
material-construction logic.

### 13. `manage_udim_texture` — P1

**Description:** Create and maintain a tiled image and its UDIM tile inventory.

**Implementation:** Create an image with tiled source, a `<UDIM>` filepath
pattern, and tiles through `image.tiles.new(tile_number, label=...)`. Validate
tile numbers and dimensions. Tile removal requires confirmation. Do not
automatically move UV islands between tiles without an explicit mapping.

### 14. `save_texture_image` — P0

**Description:** Save one generated, painted, packed, or baked image to an
explicit path.

**Implementation:** Validate format, bit depth, dimensions, destination, and
overwrite policy, then use `Image.save()` or `Image.save_render()` as
appropriate. Preserve prior filepath and image settings on failure. Return the
actual written path and file metadata.

### 15. `render_pbr_material_preview` — P0

**Description:** Render a deterministic material preview in Cycles, Eevee, or
both so engine differences are inspectable.

**Implementation:** Render in a temporary isolated scene containing named,
reusable preview geometry, neutral studio lighting, a controlled world, camera,
and color-management settings. Support sphere, plane, and rounded-cube
surfaces; bounded resolution and sample counts; transparent and opaque
backgrounds; and optional normal/displacement close-ups. Eevee and Cycles runs
must use the same camera, transforms, lights, exposure, and output transform.
Return or save each labeled image only after successful rendering. Restore
scene/window context and remove temporary datablocks in `finally`; require
confirmation for expensive Cycles previews or explicit file output.

## 3. UV tools

### 16. `manage_uv_maps` — P0

**Description:** List, create, duplicate, rename, activate, mark for rendering,
or remove UV maps.

**Implementation:** Use `mesh.uv_layers`, `UVLoopLayers.new()`, active layer
properties, and `remove()`. Reject replacement/removal without confirmation and
disclose which material nodes reference the map.

### 17. `set_uv_seams` — P0

**Description:** Mark or clear seams using explicit edge indices or
deterministic rules.

**Implementation:** Validate every edge before mutation, then set seam
attributes through mesh/BMesh data or a restored Edit Mode context with
`bpy.ops.mesh.mark_seam`. Optional rules may include boundaries, sharp edges,
or an angle threshold, but the response must return the exact changed indices.

### 18. `unwrap_uvs` — P0

**Description:** Seam-driven unwrap of explicit faces or an entire named mesh.

**Implementation:** Generalize the existing `unwrap_retopology_uvs` handler.
Support Blender 5.1 `ANGLE_BASED`, `CONFORMAL`, and `MINIMUM_STRETCH` modes
through `bpy.ops.uv.unwrap`. Create or target an explicit UV map, validate
indices before changing mode, and restore mode, selection, active object, and
active UV layer.

### 19. `project_uvs` — P1

**Description:** Deterministic Smart, Cube, Cylinder, Sphere, Lightmap, or
camera projection.

**Implementation:** Route to `bpy.ops.uv.smart_project`, `cube_project`,
`cylinder_project`, `sphere_project`, or `lightmap_pack` with method-specific
schemas. Camera projection is safer through
`bpy_extras.object_utils.world_to_camera_view` than a UI-dependent “project
from view” operator.

### 20. `optimize_uv_layout` — P0

**Description:** Normalize island scale, relax distortion, and pack into UV or
UDIM space.

**Implementation:** Compose checked calls to
`bpy.ops.uv.average_islands_scale`, `minimize_stretch`, and `pack_islands`.
Expose rotation, scale, margin method, margin, pinned-island policy, and
`udim_source`. Report `CANCELLED` as an error.

### 21. `set_uv_texel_density` — P1

**Description:** Scale UV islands to a target pixels-per-world-unit density.

**Implementation:** Compute world-space polygon area and UV area, group loops
into islands, and scale each island around its UV centroid. Require texture
resolution and target density. Report mean, minimum, maximum, and variation
before and after.

### 22. `inspect_uv_layout` — P0

**Description:** Production UV audit without changing the mesh.

**Implementation:** Report map names, island counts, bounds, zero-area faces,
overlaps, mirrored orientation, out-of-range UVs, stretch, occupied area,
padding estimate, and texel-density distribution. Reuse the analytics already
returned by `unwrap_retopology_uvs`; make overlap checks bounded and disclose
truncation.

### 23. `transfer_mesh_attributes` — Existing; reuse

**Description:** Transfer UV maps, seams, color attributes, normals, and
material indices between meshes.

**Implementation:** The existing tool already supports `UVS` and related
production data through Data Transfer. Extend only if named source/destination
UV-layer mapping is missing; do not create a redundant `transfer_uvs` tool.

## 4. Baking and paint preparation

### 24. `create_bake_cage` — Existing; reuse

**Description:** Create and validate an editable high-to-low baking cage.

**Implementation:** Retain the existing topology-preserving cage workflow. Add
no competing cage tool; expose its enclosure, ray-miss, and self-intersection
validation to the general texturing surface.

### 25. `bake_texture_map` — P0

**Description:** Bake one map atomically, either from the same object or
selected high-poly sources.

**Implementation:** Generalize `bake_retopology_maps` to support native bake
types plus semantic socket bakes for base color, metallic, opacity, and custom
scalar channels. Baking is a Cycles operation even when the resulting maps are
intended for Eevee; switch to Cycles temporarily and disclose that fact. Use
`bpy.ops.object.bake` and `BakeSettings` for
selected-to-active, cage, extrusion, ray distance, margin, pass filters, normal
space, swizzle, and UV layer. For semantic channels, duplicate or temporarily
override materials and route the selected value through Emission; never
permanently modify the originals. Restore engine, samples, selection, active
object, UV layer, and active image nodes in `finally`. Require confirmation and
an explicit output path. Default Eevee-targeted results to tangent normal and
bump/height maps instead of true displacement.

### 26. `setup_texture_paint_canvas` — P1

**Description:** Prepare an image and material paint slot without attempting
interactive brush strokes.

**Implementation:** Validate a UV map, create or reuse an image, add a managed
Image Texture node, and make it the material’s active paint target. Do not
leave Blender in Texture Paint mode or simulate mouse strokes; return the
configured object, material, UV map, image, and remaining user action.

### 27. `validate_pbr_asset` — P0

**Description:** End-to-end read-only readiness report for Cycles, Eevee, or
dual-engine rendering.

**Implementation:** Audit material slots, shader-to-output paths, image
availability, colorspaces, normal-map conversion, alpha configuration, UV
quality, texel density, UDIM coverage, dirty/unsaved images, bake padding, and
unsupported procedural nodes. Support profiles such as `BLENDER_CYCLES`,
`BLENDER_EEVEE`, and `BLENDER_BOTH`. Cycles checks include displacement setup,
bake readiness, ray visibility, and texture interpolation. Eevee checks include
unsupported displacement, transparency/surface rendering, refraction,
thickness, shadow behavior, and scene-setting requirements. The dual-engine
profile reports visual-risk differences for transmission, volume, displacement,
and procedural nodes. Return evidence and remediation, never auto-fix.

## Implementation order

1. Dual-engine material inspection/creation/assignment, images, texture-set
   application, Cycles/Eevee validation, and deterministic previews.
2. Generalize the existing unwrap and bake handlers; add mapping, UV
   optimization, texel density, storage, and Cycles-backed bake profiles for
   Eevee-ready maps.
3. Add UDIM, transactional graph patching, and texture-paint preparation.

Export-oriented channel packing is intentionally deferred. If later required,
implement `pack_pbr_channels` as an optional delivery tool rather than part of
the Cycles/Eevee material core.

## Production implementation contract

- Run every `bpy` data mutation and operator on Blender's main thread.
- Prevalidate complete inputs before the first mutation.
- Preserve and restore mode, selection, active object, active UV map, active
  image nodes, render engine, and relevant scene settings with `try`/`finally`.
- Prefer Blender data APIs; use operators only where they are the appropriate
  API and validate that their result contains `FINISHED`.
- Tag MCP-managed nodes and resources so repeated calls are idempotent and do
  not disturb user-authored graph branches.
- Track newly created datablocks and remove them on failure. Create one explicit
  undo checkpoint per mutating request.
- Require explicit paths for filesystem work, enforce size limits, and require
  confirmation before overwriting files or removing UV maps, images, or nodes.
- Return explicit changed objects/resources, retained live data, validation
  evidence, warnings, and actionable failure details.
- Paginate or bound graph inspection, image inventories, UV overlap checks, and
  pixel-processing work.

## Tools not to expose

Avoid public tools for arbitrary Python, separate node-add/connect/delete
calls, UI brush strokes, “clear all materials,” one tool per texture channel,
or separate Cycles and Eevee copies of every material tool. A semantic
texture-set tool, an engine target parameter, and one transactional graph-patch
tool provide the same capability with much stronger validation and rollback.

## Research findings

The comparable MCPs confirm demand for materials, node graphs, UVs, and baking:

- [`blender-ai-mcp`](https://github.com/PatrykIti/blender-ai-mcp/blob/main/_docs/TOOLS/MATERIAL_TOOLS_ARCHITECTURE.md)
  separates material creation, assignment, parameters, textures, and
  inspection. Its
  [UV design](https://github.com/PatrykIti/blender-ai-mcp/blob/main/_docs/TOOLS/UV_TOOLS_ARCHITECTURE.md)
  also identifies overlap, density, and distortion analysis as important gaps.
- [`blender-mcp-bridge`](https://github.com/seehiong/blender-mcp-bridge/blob/main/blender_mcp_addon/tools/materials.py)
  provides material assignment, texture-map binding, and low-level node calls.
  Its batch assignment is useful, but its fragmented node operations should
  become one validated transaction.
- [`blender-mcp-pro`](https://github.com/youichi-uda/blender-mcp-pro/blob/master/addon/handlers/uv_texture.py)
  covers multiple projection methods and baking, while its
  [shader-node handler](https://github.com/youichi-uda/blender-mcp-pro/blob/master/addon/handlers/shader_nodes.py)
  demonstrates the breadth agents need. The production implementation needs
  stronger context restoration, rollback, path handling, and graph validation.

## Primary technical sources

- [Blender 5.1 Principled BSDF](https://docs.blender.org/manual/en/5.1/render/shader_nodes/shader/principled.html)
- [Image Texture node](https://docs.blender.org/manual/en/5.1/render/shader_nodes/textures/image.html)
- [UV unwrapping](https://docs.blender.org/manual/en/5.1/modeling/meshes/uv/unwrapping/index.html)
  and [UV editing](https://docs.blender.org/manual/en/5.1/modeling/meshes/uv/editing.html)
- [UDIM workflow](https://docs.blender.org/manual/en/5.1/modeling/meshes/uv/workflows/udims.html)
- [Cycles baking](https://docs.blender.org/manual/en/5.1/render/cycles/baking.html)
- [Cycles material settings](https://docs.blender.org/manual/en/5.1/render/cycles/material_settings.html)
- [Eevee material settings](https://docs.blender.org/manual/en/5.1/render/eevee/material_settings.html)
  and [Eevee limitations](https://docs.blender.org/manual/en/5.1/render/eevee/limitations/index.html)
- [Blender 5.1 UV operators](https://docs.blender.org/api/5.1/bpy.ops.uv.html),
  [BakeSettings](https://docs.blender.org/api/5.1/bpy.types.BakeSettings.html),
  and [Image API](https://docs.blender.org/api/5.1/bpy.types.Image.html)
