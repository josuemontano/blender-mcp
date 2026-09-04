# Production-Grade Lighting Tool Plan for Cycles and EEVEE

## Recommended production tool surface

Implement 26 public tools in four groups: inspection, light construction,
environment lighting, and engine-specific verification. The default target is
`BOTH`, meaning one setup that behaves predictably in Cycles and EEVEE. Features
available in only one engine must be labeled and validated rather than silently
approximated.

The current repository does not expose general-purpose light tools. Its Poly
Haven integration downloads HDRIs and immediately rebuilds a world. Reuse its
provider/download capability, but move world construction into the generic,
non-destructive tools proposed below.

## Cycles and EEVEE compatibility contract

- Public tools that have engine-dependent behavior accept `target_engine`:
  `BOTH`, `CYCLES`, or `EEVEE`. Resolve the actual Blender 5.1 engine identifier
  through runtime RNA instead of carrying compatibility fallbacks for older
  Blender releases.
- Point, Spot, Area, and Sun lights use the shared Blender `Light` data API.
  Their numeric energy semantics still differ by type, so responses must state
  the light type, `energy`, `exposure`, `normalize`, dimensions, and scene unit
  scale instead of presenting unlike values as directly comparable.
- Cycles-only features include arbitrary light shader graphs, reliable IES
  profiles, light groups as compositing passes, shadow catchers, and detailed
  per-ray visibility. Reject or warn for unsupported EEVEE targets.
- EEVEE-specific features include Sphere, Plane, and Volume light probes,
  irradiance-cache baking, shadow-pool controls, Fast GI, and screen/probe ray
  tracing. These must not be presented as Cycles controls.
- Light linking and ordinary lamp properties should be tested in both engines.
  Validation reports any observable engine difference.
- Exposure and view transform are held constant during lighting comparisons.
  A tool must never hide a weak or clipped lighting setup by changing exposure
  automatically.
- Use positive, finite energy values for physically predictable lighting.
  Light size controls shadow softness; energy/exposure controls intensity.

## 1. Inspection and diagnosis

### 1. `list_lights` — P0

**Description:** Return a paginated inventory of light objects in the active
scene or a named collection.

**Implementation details:** Read scene objects of type `LIGHT` without changing
selection. Return object and datablock names, light type, world transform,
energy, exposure, color or temperature, shadow state, data users, collection
membership, target constraint, light group, and receiver/blocker collections.
Include type-specific summaries: Area shape and size, Point/Spot radius, Spot
cone and blend, and Sun angle. Support `limit`, `offset`, and an optional type
filter.

### 2. `inspect_light` — P0

**Description:** Inspect one light's complete effective configuration and
engine compatibility.

**Implementation details:** Read `bpy.types.Light` properties including
`energy`, `exposure`, `normalize`, `color`, `use_temperature`, `temperature`,
`diffuse_factor`, `specular_factor`, `transmission_factor`, `volume_factor`,
`use_shadow`, `use_nodes`, and `node_tree`. Add subclass properties from
`AreaLight`, `PointLight`, `SpotLight`, or `SunLight`; object transform and
constraints; `Object.light_linking`; `Object.lightgroup`; and animation data.
Summarize light nodes and IES dependencies without returning an unbounded node
tree.

### 3. `inspect_lighting_setup` — P0

**Description:** Produce a read-only scene-level lighting snapshot suitable for
planning and reproducibility.

**Implementation details:** Aggregate active render engine, scene units, camera,
color management, world/background graph, HDRI or sky settings, all lights,
emissive objects, volume materials, light links, light groups, EEVEE probes and
cache state, and relevant Cycles/EEVEE quality settings. Report hidden or
disabled lights and collection/view-layer exclusions. Keep detailed lists
paginated and include stable resource names.

### 4. `validate_lighting_setup` — P0

**Description:** Audit lighting readiness for Cycles, EEVEE, or dual-engine
delivery without modifying the scene.

**Implementation details:** Validate missing cameras, zero/negative or
non-finite power, duplicate coincident lights, lights aimed away from declared
subjects, extreme scene scale, unavailable image/IES paths, incompatible world
nodes, unintended negative lights, unbaked/stale EEVEE Volume probes, excessive
probe grids, invalid light-link collections, empty light groups, disabled
shadows, volume cost risks, and exposure/clipping risks. `BOTH` mode reports
Cycles/EEVEE differences for mesh emission, IES, world sun discs, volumes,
reflections, ray visibility, and indirect lighting. Return evidence and
remediation; never auto-fix.

## 2. Light creation and control

### 5. `create_light` — P0

**Description:** Create one named Point, Spot, Area, or Sun light with explicit
physical and organizational settings.

**Implementation details:** Prefer `bpy.data.lights.new(name, type)` and
`bpy.data.objects.new` over `bpy.ops.object.light_add`, then link the object to
an explicit collection such as `Lighting`. Accept world-space location and
rotation, energy, exposure, normalization, RGB or Kelvin temperature, shadow
state, contribution factors, and type-specific properties. Validate type,
ranges, finite transforms, names, and collection before creating either
datablock. Return both object and light-data names.

### 6. `configure_light` — P0

**Description:** Patch common and type-specific properties of an existing light.

**Implementation details:** Update only supplied fields through the light data
API. Common fields include energy, exposure, normalize, color/temperature,
diffuse/specular/transmission/volume factors, custom cutoff distance, and
shadows. Support `AreaLight.shape`, `size`, `size_y`, and `spread`;
`PointLight.shadow_soft_size` and `use_soft_falloff`; Spot radius, `spot_size`,
`spot_blend`, and cone display; and `SunLight.angle`. Reject fields that do not
apply to the actual light type. Do not expose an arbitrary `property` string.

### 7. `aim_light` — P0

**Description:** Aim a directional light at a world point, object origin, named
bone, or evaluated object-bounds point.

**Implementation details:** Blender lights emit along local `-Z`; calculate a
world-space quaternion with a documented up axis and preserve parent
transforms. Support `STATIC_ROTATION` and a reversible `TRACK_TO` or
`DAMPED_TRACK` constraint. For a live target, create or reuse a named helper
Empty in a dedicated helpers collection. Validate coincident source/target
positions and return the resulting direction and constraint.

### 8. `keyframe_light` — P1

**Description:** Keyframe light transforms and photometric properties across
explicit frames.

**Implementation details:** Support object location/rotation and light-data
energy, exposure, color, temperature, size, Sun angle, and Spot cone fields.
Prevalidate every frame/value before inserting the first keyframe. Use
`keyframe_insert` on the correct Object or Light datablock, set interpolation
through resulting F-curves, and restore the original frame. Do not provide a
free-form data path.

### 9. `create_light_rig` — P1

**Description:** Create an editable, target-relative rig for common studio
lighting patterns.

**Implementation details:** Offer a small set of production presets such as
`THREE_POINT`, `PRODUCT`, `PORTRAIT`, and `SOFTBOX_PAIR`. Compute positions from
the subject's evaluated world-space bounds and camera basis rather than fixed
coordinates. Use Area lights by default, express fill/rim levels as exposure
offsets from the key, create a target Empty, add live constraints, and place all
resources in a named collection. Tag created resources so reruns update the rig
idempotently. Return every chosen transform and intensity; presets must not be
opaque.

### 10. `configure_light_shader` — P1, Cycles-first

**Description:** Configure a bounded node-based light shader for an IES profile,
gobo, or controlled emission network.

**Implementation details:** Enable `Light.use_nodes`, preserve unrelated nodes,
and ensure a valid `ShaderNodeOutputLight` path. For IES, validate an explicit
file or Text datablock and configure `ShaderNodeTexIES.filepath`/`mode`, routing
its factor to emission strength. For image gobos, build a documented managed
subgraph with projection controls. Tag all managed nodes and roll back on
failure. Treat node-based distribution as Cycles-first and return a clear EEVEE
unsupported/approximation warning after runtime verification.

### 11. `configure_emissive_object` — P1

**Description:** Turn existing mesh geometry into an intentionally managed
emissive source.

**Implementation details:** Create or patch a named material with Principled
Emission or an Emission shader, set color/temperature and strength, and assign
it through an explicit slot policy. Preserve other slots. In Cycles, report
multiple-importance-sampling and mesh-density considerations. In EEVEE, state
that visible emission does not guarantee equivalent illumination; optionally
create a tagged, parented Area-light proxy only when explicitly requested.

### 12. `configure_light_linking` — P0

**Description:** Restrict which objects receive a light and which objects block
it.

**Implementation details:** Assign explicit collections to
`light_object.light_linking.receiver_collection` and `blocker_collection`.
Prevalidate all collections and member objects before mutation. Optionally
create dedicated collections, but never move or unlink objects implicitly;
collection membership changes must be requested explicitly. Return the full
effective receiver and blocker sets and engine-support warnings.

### 13. `manage_light_groups` — P1, Cycles

**Description:** Assign lights or emissive objects to Cycles light groups and
manage their View Layer passes.

**Implementation details:** Set `Object.lightgroup`, inspect
`ViewLayer.lightgroups`, and use checked
`bpy.ops.scene.view_layer_add_lightgroup` only with a valid scene/view-layer
override. Support list, create, assign, and unassign operations. Removing a
group or pass requires confirmation. Report that light groups are a Cycles
compositing workflow rather than an EEVEE parity feature.

### 14. `configure_object_lighting_visibility` — P1, Cycles-first

**Description:** Configure object participation in lighting and compositing
rays.

**Implementation details:** Patch explicitly named objects using
`visible_camera`, `visible_diffuse`, `visible_glossy`, `visible_shadow`,
`visible_transmission`, `visible_volume_scatter`, and `is_shadow_catcher` where
supported. Validate every object before changing the first. Return which fields
are Cycles-only or ignored by EEVEE. Do not combine this with ordinary viewport
visibility.

## 3. World, environment, and atmosphere

### 15. `configure_world_background` — P0

**Description:** Create, assign, or patch a simple world Background light.

**Implementation details:** Operate on `bpy.context.scene.world`, never
`bpy.data.worlds[0]`. Create a collision-safe World only when requested, enable
nodes, and ensure a managed `ShaderNodeBackground` connected to
`ShaderNodeOutputWorld` without clearing unrelated nodes. Patch color and
strength. Support transparent film as a separate explicit setting because it
changes camera visibility but not necessarily world illumination.

### 16. `configure_hdri_environment` — P0

**Description:** Configure a stable HDR/EXR environment for illumination,
reflections, and background appearance.

**Implementation details:** Load an explicit persistent path with
`bpy.data.images.load(check_existing=True)`, configure a managed
`ShaderNodeTexEnvironment`, Texture Coordinate and Mapping/Vector Rotate nodes,
Background, and World Output. Support equirectangular or mirror-ball projection,
rotation, strength, image reuse, and an explicit replacement policy. Respect
the active OCIO configuration for scene-linear HDR data; do not blindly label
HDR/EXR as Non-Color. Do not delete temporary source files while the image still
depends on them. Poly Haven downloads should feed this tool rather than mutate
the world directly.

### 17. `configure_procedural_sky` — P0

**Description:** Configure a physical sky and optionally synchronize a Sun
light for cross-engine consistency.

**Implementation details:** Create or reuse a managed `ShaderNodeTexSky`,
Background, and World Output. Expose the Blender 5.1 sky model and its verified
properties, including `sky_type`, sun elevation/rotation or direction,
`altitude`, air/dust/ozone density, sun size, sun intensity, and sun disc.
Because the sky sun disc is documented as Cycles-only, `target_engine=BOTH`
should optionally create/update a named Sun light with matching direction and
angular size for EEVEE. Warn against accidental double direct-light energy.

### 18. `configure_atmosphere_volume` — P1

**Description:** Add bounded fog/haze or a world atmosphere with controlled
scattering.

**Implementation details:** Support `WORLD` scope by connecting a managed
Principled Volume to World Output Volume, and `BOUNDED_BOX` scope by creating a
named cube and volume-only material in a dedicated collection. Expose density,
anisotropy, absorption color, emission, and temperature. Keep density ranges
bounded, report world-volume performance risk, and configure no engine quality
settings implicitly. EEVEE volume sampling belongs to
`configure_lighting_quality`.

## 4. EEVEE probes, quality, and verification

### 19. `create_light_probe` — P1, EEVEE

**Description:** Create a Sphere, Plane, or Volume probe with an explicit role
and collection.

**Implementation details:** Use `bpy.data.lightprobes.new` and object creation
when supported, otherwise a validated `bpy.ops.object.lightprobe_add` context.
Set world-space transform and type-appropriate initial dimensions. Reject probe
creation for a Cycles-only request and state that probes are EEVEE resources.

### 20. `configure_light_probe` — P1, EEVEE

**Description:** Patch the type-specific influence, clipping, parallax, and
quality settings of an EEVEE probe.

**Implementation details:** For Sphere probes configure influence distance,
falloff, clipping, influence shape, and parallax settings. For Plane probes
configure distance, falloff, and clipping through properties verified at
runtime. For Volume probes configure `resolution_x/y/z`, `bake_samples`,
intensity, normal/view bias, validity threshold, and dilation controls.
Calculate and report total sample count and estimated cost before accepting a
large volume grid.

### 21. `manage_light_probe_cache` — P1, EEVEE

**Description:** Inspect, bake, or explicitly free EEVEE irradiance-volume
caches.

**Implementation details:** Support `INSPECT`, `BAKE`, and `FREE`. Baking uses
`bpy.ops.object.lightprobe_cache_bake` with a correct scene, view layer,
selection, active object, and mode override; require `FINISHED`, confirmation,
a bounded timeout, and progress reporting. Freeing uses
`bpy.ops.object.lightprobe_cache_free` and requires confirmation. Preserve user
selection/mode and return affected probes and cache state.

### 22. `configure_lighting_quality` — P0

**Description:** Patch only the Cycles or EEVEE render settings that materially
affect lighting quality and performance.

**Implementation details:** For Cycles, validate and patch sampling, adaptive
threshold, denoising, light-sampling threshold, direct/indirect clamps, diffuse,
glossy, transmission, transparent and volume bounces, and device through
`scene.cycles` runtime RNA. For EEVEE, patch render samples, light threshold,
shadow pool/resolution/ray/step settings, ray-tracing method/options, Fast GI,
probe resolution, indirect clamps, and volume quality through `scene.eevee`.
Optional `PREVIEW`, `BALANCED`, and `FINAL` presets must expand to explicit
values in the response and never overwrite output resolution or paths.

### 23. `configure_color_management` — P0

**Description:** Set a reproducible display transform and exposure for lighting
evaluation.

**Implementation details:** Patch `scene.view_settings.view_transform`, `look`,
`exposure`, and `gamma` only when supplied. Validate names against the active
OCIO configuration. Treat exposure as stops and report its `2^exposure`
multiplier. Do not change light energy to compensate automatically. Return the
complete before/after color-management snapshot.

### 24. `render_lighting_preview` — P0

**Description:** Render a bounded still for one engine or matched Cycles/EEVEE
comparison.

**Implementation details:** Require an explicit camera and frame. Support
`CYCLES`, `EEVEE`, or `BOTH`; for `BOTH`, preserve identical camera, resolution,
world, lights, exposure, and view transform while changing only engine-specific
quality settings. Use `bpy.ops.render.render` on the main thread, check
`FINISHED`, and restore engine, filepath, resolution, samples, frame, and render
slots in `finally`. Return inline bounded previews or write only to explicit
paths with overwrite confirmation. Long Cycles renders require confirmation.

### 25. `analyze_lighting_render` — P1

**Description:** Measure exposure and contrast from a Render Result or named
image instead of judging success from configuration alone.

**Implementation details:** Read bounded image pixels and calculate linear
luminance percentiles, mean/median, dynamic range, clipped-black and
clipped-highlight percentages, channel clipping, and optional normalized image
regions. Provide histogram data and warnings without changing exposure. For a
matched Cycles/EEVEE pair, report numeric differences but do not claim perceptual
equivalence. Copy pixels on the main thread and perform CPU analysis in a
worker.

### 26. `remove_lighting_resources` — P1

**Description:** Safely remove explicitly named lights, probes, helper targets,
or an MCP-managed light rig.

**Implementation details:** Require `confirm=True` and either exact resource
names or a tagged MCP rig identifier. Preflight object/data users, constraints,
collections, light links, light groups, and probe caches. Unlink and remove only
the requested objects; remove light/probe datablocks only when they have no
remaining users. Never purge or delete an entire generic collection by name.
Report everything removed and anything retained because it is shared.

## Implementation order

1. Add `list_lights`, `inspect_light`, `inspect_lighting_setup`, `create_light`,
   `configure_light`, `aim_light`, world background/HDRI/sky controls, color
   management, previews, and validation.
2. Add light rigs, linking, Cycles light groups and ray visibility, engine-aware
   quality settings, render analysis, and safe cleanup.
3. Add Cycles light shaders/IES, emissive-object workflows, atmosphere volumes,
   EEVEE probes, and probe-cache operations.

## Production implementation contract

- Run all `bpy` mutations and operators on Blender's main thread. Network and
  file transfer may happen in workers, but datablock creation may not.
- Prevalidate complete inputs before the first mutation. Reject invalid object,
  light, collection, frame, path, enum, range, and non-finite numeric values.
- Preserve and restore selection, active object, mode, frame, render engine,
  camera, render path, resolution, color management, and temporary overrides
  with `try`/`finally`.
- Prefer Blender data APIs. When an operator is appropriate, provide its exact
  context and require `{'FINISHED'}`; `CANCELLED` is an error.
- Keep light rigs, targets, proxy lights, probes, and atmosphere helpers in
  named collections and tag MCP-managed resources for idempotent updates.
- Do not clear a world or light node tree. Modify a tagged managed subgraph and
  preserve user-authored branches unless an explicit replacement policy is
  confirmed.
- Treat HDRIs and IES files as durable dependencies: validate paths, sizes, and
  formats; do not leave datablocks pointing at deleted temporary files.
- Probe baking, cache freeing, destructive cleanup, and long renders require
  confirmation. Provide bounded timeouts and progress where possible.
- Return changed objects/resources, retained live constraints and node graphs,
  engine support, warnings, verification evidence, and next useful actions.
- Add Blender 5.1 runtime tests for world transforms, parented lights, each
  light type, light linking, Cycles/EEVEE renders, and EEVEE probe cache state.

## Tools not to expose

Avoid separate tools for each light type, a free-form `set_light_property`, raw
light-node CRUD, arbitrary Python, hidden auto-exposure, fixed-coordinate
three-point presets, and a generic “make lighting cinematic” action. The typed
creation/configuration tools and one transparent rig builder cover these cases
with safer schemas and inspectable results.

Do not duplicate ordinary animation, collection, transform, or render-output
tools inside the lighting module. Lighting tools should call or share those
validated helpers.

## Comparable MCP findings

- [`blender-ai-mcp`](https://github.com/PatrykIti/blender-ai-mcp/blob/main/blender_addon/application/handlers/scene_world_render_mixin.py)
  emphasizes reconstructable render, world, and color-management snapshots.
  Its inspect/configure separation is valuable, but it does not provide a
  comprehensive light-object workflow.
- [`blender-mcp-bridge`](https://github.com/seehiong/blender-mcp-bridge/blob/main/blender_mcp_addon/tools/lighting.py)
  exposes create/configure-light and color/sky/HDRI world modes. These confirm
  the core demand, but production code should not clear world nodes or select
  `bpy.data.worlds[0]`. Its
  [rendering tools](https://github.com/seehiong/blender-mcp-bridge/blob/main/blender_mcp_addon/tools/rendering.py)
  also demonstrate the value of temporary, cleaned-up preview lighting.
- [`blender-mcp-pro`](https://github.com/youichi-uda/blender-mcp-pro/blob/master/addon/handlers/lights.py)
  provides list/create/configure/delete operations and a three-point preset.
  The useful concepts are inspection and target-relative rigs; a raw property
  setter, fixed offsets, destructive deletion, and compatibility code for old
  EEVEE APIs are not appropriate for this Blender 5.1-only server. Its
  [render handler](https://github.com/youichi-uda/blender-mcp-pro/blob/master/addon/handlers/render.py)
  reinforces the need for checked render results and complete state
  restoration.

## Primary Blender 5.1 sources

- [Light Objects](https://docs.blender.org/manual/en/5.1/render/lights/light_object.html)
- [Cycles Light Settings](https://docs.blender.org/manual/en/5.1/render/cycles/light_settings.html)
- [EEVEE Light Settings](https://docs.blender.org/manual/en/5.1/render/eevee/light_settings.html)
- [Light Linking](https://docs.blender.org/manual/en/5.1/render/lights/light_linking.html)
- [Environment Texture](https://docs.blender.org/manual/en/5.1/render/shader_nodes/textures/environment.html)
- [Sky Texture](https://docs.blender.org/manual/en/5.1/render/shader_nodes/textures/sky.html)
- [IES Texture](https://docs.blender.org/manual/en/5.1/render/shader_nodes/textures/ies.html)
- [Principled Volume](https://docs.blender.org/manual/en/5.1/render/shader_nodes/shader/volume_principled.html)
- [EEVEE Light Probes](https://docs.blender.org/manual/en/5.1/render/eevee/light_probes/index.html)
- [EEVEE Ray Tracing](https://docs.blender.org/manual/en/5.1/render/eevee/render_settings/raytracing.html)
- [Cycles Sampling](https://docs.blender.org/manual/en/5.1/render/cycles/render_settings/sampling.html)
  and [Light Paths](https://docs.blender.org/manual/en/5.1/render/cycles/render_settings/light_paths.html)
- Blender Python API:
  [`Light`](https://docs.blender.org/api/5.1/bpy.types.Light.html),
  [`AreaLight`](https://docs.blender.org/api/5.1/bpy.types.AreaLight.html),
  [`SpotLight`](https://docs.blender.org/api/5.1/bpy.types.SpotLight.html),
  [`SunLight`](https://docs.blender.org/api/5.1/bpy.types.SunLight.html),
  [`World`](https://docs.blender.org/api/5.1/bpy.types.World.html),
  [`ObjectLightLinking`](https://docs.blender.org/api/5.1/bpy.types.ObjectLightLinking.html),
  [`ViewLayer`](https://docs.blender.org/api/5.1/bpy.types.ViewLayer.html),
  [`Object`](https://docs.blender.org/api/5.1/bpy.types.Object.html),
  [`SceneEEVEE`](https://docs.blender.org/api/5.1/bpy.types.SceneEEVEE.html),
  [`BlendDataProbes`](https://docs.blender.org/api/5.1/bpy.types.BlendDataProbes.html),
  [`LightProbeVolume`](https://docs.blender.org/api/5.1/bpy.types.LightProbeVolume.html),
  [`ShaderNodeTexIES`](https://docs.blender.org/api/5.1/bpy.types.ShaderNodeTexIES.html),
  [`ShaderNodeTexSky`](https://docs.blender.org/api/5.1/bpy.types.ShaderNodeTexSky.html),
  and [`ColorManagedViewSettings`](https://docs.blender.org/api/5.1/bpy.types.ColorManagedViewSettings.html).
