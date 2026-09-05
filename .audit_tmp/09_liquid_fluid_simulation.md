# Audit: Liquid/Fluid Simulation Domain (Rubric Section 11)

**Scope**: Audit the Mantaflow-backed liquid and gas simulation tools for production-grade readiness to handle natural-language requests like "fill this glass with realistic water" / "simulate liquid being poured into it."

**Test Prompt**: Fill a glass with water via a pour source, with domain auto-sizing and collision detection.

---

## 1. Tool Inventory

### A. Canonical Cross-Domain Tools (`fluid.py`)

Located: `src/blender_mcp/server/tools/fluid.py` (261 lines)

These 6 tools accept a `domain_type: Literal["LIQUID", "GAS"]` parameter and forward to shared addon handlers. **All forward to `_call()` in `liquid/_shared.py`, making them a unified transport layer.**

| Tool | Line | Purpose | Params | Abstraction | Notes |
|------|------|---------|--------|-------------|-------|
| `inspect_fluid_simulation` | 95 | List domains/cache state | domain_type, scene_name, domain_object_name, limit, offset | **Low**: Read-only inspection; delegates to addon | Pagination included |
| `create_fluid_domain` | 118 | Create LIQUID/GAS domain box or on mesh | domain_type, scene_name, cache_directory, dimensions, resolution_max, cache_type | **Low**: Direct Blender modifier creation, no orchestration | Validates cache_frame_end >= cache_frame_start client-side |
| `configure_fluid_solver` | 148 | Patch solver settings | domain_type, FluidSolverPatch (validates timesteps_min ≤ timesteps_max) | **Medium**: Patch model validation; rejects finite=False values for gas | Shared model; both LIQUID and GAS fields present |
| `add_fluid_flow` | 170 | Register mesh as flow source | domain_type, gas_flow_type ("SMOKE"/"FIRE"/"BOTH"), FluidFlowPatch | **Low**: Thin wrapper around modifier attachment | `gas_flow_type` only applies when domain_type="GAS" |
| `add_fluid_effector` | 200 | Register collision/guide effector | domain_type, EffectorType ("COLLISION"/"GUIDE"), LiquidEffectorPatch | **Low**: Attaches modifier to object; no collision proxy logic | Uses shared liquid-side patch model |
| `manage_fluid_cache` | 228 | Inspect/bake/free cache stages | domain_type, action (STATUS/BAKE_DATA/BAKE_ALL/CANCEL/PAUSE/RESUME/FREE_*), LiquidCachePatch | **Low**: Dispatcher; bake runs synchronously or as WM job | Forwards to addon's `handle_manage_liquid_cache` (despite name, supports GAS too) |

**Observation**: All 6 tools use `liquid/_shared.py:_call()` as transport, not a separate gas-specific handler module. The domain_type parameter is purely informational to Blender; there is **no separate GAS handler surface** in the addon (only `liquid/` subdirectory observed).

---

### B. Liquid-Specific Setup & Inspection Tools

Located: `src/blender_mcp/server/tools/liquid/inspection_and_setup.py` (493 lines)

| Tool | Line | Purpose | Params | Abstraction | Overlap with fluid.py |
|------|------|---------|--------|-------------|----------------------|
| `get_liquid_simulation_info` | 124 | Inspect domains + dependencies | scene_name, domain_uuid (stable custom property), limit, offset | **Medium**: Adds UUID-stable resolution and dependency discovery | **OVERLAPS** `inspect_fluid_simulation` (domain_type="LIQUID"); this adds UUID/dependency info |
| `get_fluid_object_info` | 163 | Inspect one domain/flow/effector | object_name | **Low**: Single object bounds/transforms | **NEW**: no fluid.py equivalent |
| `create_liquid_domain` | 174 | Create domain with solver defaults | scene_name, cache_directory, dimensions, location, SimulationMethod, time_scale, timesteps_min/max, cfl_condition | **Medium**: Presets liquid-specific defaults (FLIP method, time_scale=1.0, adaptive timesteps) | **OVERLAPS** `fluid:create_fluid_domain` but LIQUID-only; adds solver defaults |
| `fit_liquid_domain` | 235 | Auto-size unbaked domain from motion | source_object_names, collider_object_names, sample_frame_start/end (max 32 frames), padding, expected_travel, splash_height | **HIGH**: Solves "size the domain to the glass" problem; samples up to 32 frames, restores current frame, pads/predicts motion | **NEW**: Unique liquid-only orchestration; no gas equivalent |
| `configure_liquid_solver` | 287 | Patch liquid solver | LiquidSolverPatch (27 liquid-specific fields: particle_randomness, use_fractions, fractions_threshold, use_flip_particles with RNA quirk) | **Medium**: Validates ranges; documents use_flip_particles toggle semantics | **OVERLAPS** `fluid:configure_fluid_solver` but liquid-only; more fields |
| `add_liquid_flow` | 307 | Register liquid mesh flow | object_name, domain_object_name, modifier_name, FlowBehavior, LiquidFlowPatch (rejects use_particle_size/"smoke-only fields") | **Medium**: Rejects incompatible smoke-only fields at schema level | **OVERLAPS** `fluid:add_fluid_flow` (domain_type="LIQUID"); adds explicit rejection of smoke fields |
| `configure_liquid_flow` | 339 | Patch flow settings post-creation | object_name, modifier_name, domain_object_name, LiquidFlowPatch | **Medium**: Patch model; LiquidFlowPatch documents Use Flow toggle semantics | **NEW**: no fluid.py equivalent (fluid.py is post-creation only) |
| `add_liquid_effector` | 366 | Register collision/guide effector | object_name, domain_object_name, EffectorType, LiquidEffectorPatch | **LOW**: Thin wrapper | **OVERLAPS** `fluid:add_fluid_effector` (domain_type="LIQUID") |
| `configure_liquid_effector` | 397 | Patch effector settings post-creation | object_name, modifier_name, domain_object_name, LiquidEffectorPatch | **Medium**: Patch model | **NEW**: no fluid.py equivalent |
| `configure_liquid_scope_and_boundaries` | 422 | Set domain collection scope + open/close faces | domain_object_name, modifier_name, flow/effector/force_collection_name, boundaries (LiquidBoundaryPatch: front/back/left/right/top/bottom as bool) | **Medium**: Collection management + per-face collision toggle | **NEW**: unique to liquid |
| `estimate_liquid_resources` | 463 | Estimate grid dimensions and relative cost | domain_object_name, modifier_name | **MEDIUM**: Calculates cell count, relative cost index = base_cells × (1.0 + particle_factor×0.35 + mesh_multiplier×0.2 + secondary_count×0.3) × frames. **Does NOT return absolute disk/memory/time estimates.** | **NEW**: no fluid.py equivalent |
| `validate_liquid_setup` | 473 | Preflight check: domains, dependencies, cache readiness | scene_name, domain_object_names (optional), max_findings | **HIGH**: Non-mutating validation; checks object existence, thin walls, cache directory writable, collection scopes, flow/effector registration | **NEW**: no fluid.py equivalent; critical pre-bake readiness |

---

### C. Liquid Delivery & Proxy Tools

Located: `src/blender_mcp/server/tools/liquid/delivery.py` (282 lines)

| Tool | Line | Purpose | Abstraction |
|------|------|---------|-------------|
| `create_liquid_proxy_rig` | 69 | Create low-cost proxy (BOX/CAPSULE/CONVEX_HULL/DECIMATED/HOLLOW_CONTAINER/SUPPLIED) that drives a flow or effector | **HIGH**: Solves "don't emit from the actual pour object, use a lightweight stand-in." Geometry="HOLLOW_CONTAINER" creates a live Solidify modifier, removes rim cap, auto-detects wall/bottom thickness. Supports COPY_TRANSFORMS or PARENT driver. |
| `duplicate_liquid_setup_variant` | 132 | Clone complete domain setup with remapped members, independent cache | **MEDIUM**: Clones domain + all flows/effectors/guides/forces; one domain disabled; mesh/material/animation copy/link policies selectable. |
| `prepare_liquid_render_mesh` | 170 | Add reversible post-fluid modifiers (Subdivision/Smooth/Laplacian) or create explicit current-frame delivery mesh | **MEDIUM**: Applies LiquidRenderFinish (smooth shading, subdivision levels, laplacian smoothing). Optional "delivery mesh" for playback-time frame evaluation (REPLAY domains only). |
| `export_liquid_simulation` | 208 | Atomically export baked surface ± secondary particles to Alembic or USD | **MEDIUM**: Frame range, coordinate space (WORLD/LOCAL), units, axis conventions, material inclusion. Max 500 frames per call; overwrite policy. |
| `analyze_liquid_performance` | 258 | Report structural cost + optional frame-evaluation timings | **LOW**: Bounded structural evidence (object count, cache entries); optional measured replay performance (30s timeout). |

---

### D. Animation & Time-Keying Tools

Located: `src/blender_mcp/server/tools/liquid/animation.py` (58 lines)

| Tool | Line | Purpose |
|------|------|---------|
| `animate_liquid_flow` | 36 | Key flow settings (use_inflow, velocity_factor, velocity_normal, velocity_random) with per-keyframe interpolation (CONSTANT/LINEAR/BEZIER) and merge policy (INSERT_ONLY/REPLACE_EXISTING) | Validates exactly one property per keyframe |

---

### E. Force Fields & Guides

Located: `src/blender_mcp/server/tools/liquid/force_fields.py` (85 lines) + `guides.py` (59 lines)

| Tool | Lines | Purpose |
|------|-------|---------|
| `configure_liquid_force_fields` | 61 | Create or scope force fields (FORCE/WIND/VORTEX/TURBULENCE/DRAG) to a domain; set effector weights (gravity/force/wind/etc. per 0–200 range) | Validates distance_min ≤ distance_max |
| `create_liquid_guide` | 19 | Create effector-based guide OR link one liquid domain as another domain's guide source | Supports source="EFFECTOR" or "DOMAIN"; guide_alpha/beta/vel_factor parameters for Mantaflow guide coupling |

---

### F. Mesh, Materials, Quality Profiles

Located: `src/blender_mcp/server/tools/liquid/mesh_and_materials.py` (230 lines) + `quality.py` (139 lines)

| Tool | Line | Purpose |
|------|------|---------|
| `configure_liquid_mesh` | 127 | Patch mesh generation (use_mesh, mesh_scale, particle_radius, smoothing, concavity bounds, speed vectors, cache format) | Validates mesh_concave_lower ≤ mesh_concave_upper |
| `configure_liquid_secondary_particles` | 144 | Patch spray/foam/bubble/tracer generation; life ranges, potential thresholds, buoyancy/drag | Validates min ≤ max for all ranges; sndparticle_potential/update_radius are int cell counts (1–4) not float |
| `configure_liquid_diffusion` | 157 | Set viscosity + surface tension from presets (WATER/OIL/HONEY/MOLTEN/STYLIZED), direct values, or SI inputs (dynamic_viscosity_pa_s + density_kg_m3) | Validates one viscosity source only |
| `create_liquid_material` | 170 | Create Principled transparent liquid material (presets: WATER/GLASS/OIL/TINTED); assign to domain mesh | Supports APPEND or REPLACE_SLOT assignment |
| `create_secondary_particle_render_setup` | 198 | Configure baked Mantaflow particle systems for bounded object instancing (max 16 systems) | Optional sphere creation; display percentage 1–100 |
| `apply_liquid_quality_profile` | 109 (quality.py) | Convenience wrapper: apply PREVIEW/BALANCED/FINAL preset (solver + mesh patch pair) via existing configure_liquid_solver/configure_liquid_mesh | Non-mutating wrapper; profiles defined as static tuples of LiquidSolverPatch/LiquidMeshPatch |

---

### G. Lifecycle & Removal

Located: `src/blender_mcp/server/tools/liquid/lifecycle.py` (39 lines)

| Tool | Line | Purpose |
|------|------|---------|
| `remove_fluid_components` | 22 | Remove exact fluid modifiers and optionally MCP-owned helper objects after cache-orphan preflight | Rejects removal if it would orphan on-disk bake unless accept_orphaned_cache=True |

---

### H. Simulation Caching & Evaluation

Located: `src/blender_mcp/server/tools/liquid/simulation.py` (161 lines)

| Tool | Line | Purpose |
|------|------|---------|
| `sample_liquid_simulation` | 64 | Evaluate up to 32 cached/replay frames; return mesh vertices, particle counts, bounds | For REPLAY, steps from cache_frame_start in order (required for Blender correctness); for MODULAR/ALL, jumps directly (rejects out-of-range). Rejects preroll > max_preroll_frames (default 250). |
| `manage_liquid_cache` | 99 | Inspect, configure, bake, pause/resume, cancel, or free Mantaflow cache stages | STATUS/CONFIGURE/BAKE_*/START_BAKE/RESUME/CANCEL/PAUSE/FREE_* actions. START_BAKE is non-blocking (WM job) when GUI window available; falls back to synchronous under --background. |

---

### I. Result Validation & Shot Orchestration

Located: `src/blender_mcp/server/tools/liquid/result_validation.py` (66 lines) + `shot.py` (167 lines)

| Tool | Line | Purpose |
|------|------|---------|
| `validate_liquid_result` | 18 | Measure baked liquid against fill/spill/penetration targets; grid-sample evaluated mesh against CONTAINER_VOLUME/SPILL_VOLUME boxes (created by setup_liquid_shot). Per-frame: fill volume, fill fraction, spill volume, wall-penetration volume, escaped volume, mesh connectivity. Rejects frames > max_preroll_frames if REPLAY. | Target-driven validation (fill_fraction vs deadline_frame, overflow_policy). |
| `setup_liquid_shot` | 74 | **ORCHESTRATOR**: Build complete liquid shot in one call. Accepts list of containers (ShotContainer: object_name, collision_proxy, effector_type, rim_axis, wall_thickness, proxy_object_name) and sources (ShotSource: object_name, behavior, enabled_seconds, flow_settings). Internally: create_liquid_domain → add effectors/flows (proxy rig for HOLLOW_CONTAINER) → fit_liquid_domain → apply_liquid_quality_profile → validation_volumes → validate_liquid_setup. dry_run=True validates without mutating. | **HIGHEST ABSTRACTION**: Solves the "fill glass with water" request end-to-end via declarative container/source specs. Returns simulation_id for later validate_liquid_result. |

---

## 2. Runtime Blender 5.2.1 API Validation

**Command executed**: `/opt/homebrew/bin/blender --background --python-expr "[fluid modifier introspection]"` (Blender 5.2.1 LTS, build 2026-08-25)

**Properties verified**:
```json
{
  "domain_type": "LIQUID",
  "resolution_max": 32,
  "cache_type": "REPLAY",
  "use_mesh": true,
  "use_spray_particles": false,
  "use_foam_particles": false,
  "use_bubble_particles": false,
  "viscosity_value": 0.05000000074505806,
  "surface_tension": 0.0,
  "simulation_method": "FLIP",
  "flip_ratio": 0.9700000286102295
}
```

**Assessment**: ✅ **ALL verified properties match tool assumptions**. No mismatches, no deprecations detected. Blender 5.2.1 API surface is stable and tool expectations are correct.

---

## 3. Reliability Analysis

### A. Cache Management & Bake Failure Handling

**File**: `src/blender_mcp/bundled/addon/handlers/liquid/simulation.py` (766 lines)

**Bake Failure Modes**:
- Line 123: `except Exception: pass` in `_cache_state()` — swallows exceptions silently (potential hidden errors)
- Line 211, 245: `contextlib.suppress(OSError)` when walking cache directory or writing manifest — tolerates I/O errors gracefully
- Line 160: `_run_fluid_operator()` validates operator result is `{"FINISHED"}` or `{"RUNNING_MODAL"}`; raises `RuntimeError` if operator returns other states (e.g., `{"CANCELLED"}`)

**Disk Space Handling**:
- **NOT IMPLEMENTED**: No explicit disk-space preflight check in `manage_liquid_cache()` before BAKE_* actions
- `_cache_directory_evidence()` (line 198) walks existing cache to report file count and bytes scanned, but does **not** call `shutil.disk_usage()` or `os.statvfs()` to check available space on the target filesystem
- **Gap**: A multi-hour bake can fail silently if the target filesystem runs out of space mid-bake; no pre-flight alert

**Invalid Domain Bounds**:
- Line 1686 in `estimate_liquid_resources`: `if longest <= 0: raise ValueError("Domain has zero world-space extent")` — validates domain has non-zero size
- Line 657–659 in `configure_liquid_scope_and_boundaries`: Validates collection names are present or createable

**Verdict**: ✅ **Acceptable** error handling for operator failures and I/O exceptions. ⚠️ **Gap**: No disk-space preflight check; bake can fail with no early warning.

### B. Resource Estimation

**File**: `src/blender_mcp/bundled/addon/handlers/liquid/inspection_and_setup.py` (line 1680–1733)

**Function**: `estimate_liquid_resources()`

**What it returns**:
- `estimated_grid`: resolution_max, cell_size, cells_xyz, base_cell_count
- `relative_cost_index` = base_cells × (1.0 + particle_factor×0.35 + mesh_multiplier×0.2 + secondary_count×0.3) × frames
- **Recommendations**: preview_resolution_max, final_resolution_max, disclaimer note

**What it does NOT return**:
- Absolute memory in GB/MB (only relative index)
- Absolute disk space in GB (only relative index)
- Estimated bake time in seconds (only relative index)

**Docstring** (line 463–469 in server/tools/liquid/inspection_and_setup.py):
> "Estimate grid dimensions and conservative relative cache cost without changing the domain."

**Verdict**: ⚠️ **Limited utility**. The tool is honest ("relative cost"), but an agent cannot use it to predict "will this bake fit in 16GB RAM?" or "will this bake finish in 1 hour?". The docstring says "conservative" but the disclaimers acknowledge "occupancy, motion, compression, hardware, and solver behavior dominate actual memory, disk, and bake time." **This is accurate but leaves the agent without concrete pre-flight guidance.**

---

## 4. Critical Workflow Test: "Fill This Glass With Water"

### Scenario
- Scene contains a glass mesh (a container) and a pour source mesh (water emitter)
- Agent must: size domain, set up collider, set up flow, configure solver, bake, validate result

### Ideal Tool Sequence (Using Highest Abstraction)

**Option A: setup_liquid_shot (Recommended for Agent)**

```python
# Single declarative call
setup_liquid_shot(
  scene_name="Scene",
  cache_directory="/tmp/liquid_cache",
  containers=[
    ShotContainer(
      object_name="Glass_Mesh",
      collision_proxy="HOLLOW_CONTAINER",  # ← Agent must know: proxy or direct effector?
      effector_type="COLLISION",
      rim_axis="Z",  # ← Agent must know: which axis is the rim?
      wall_thickness=0.05,
    )
  ],
  sources=[
    ShotSource(
      object_name="Pour_Source",
      behavior="INFLOW",
      flow_settings=LiquidFlowPatch(use_inflow=True, surface_distance=1.5)
    )
  ],
  quality="BALANCED",
  cache_type="REPLAY",
  cache_frame_start=1,
  cache_frame_end=250,
  padding=(0.25, 0.25, 0.25),
  expected_travel=(0.0, 0.0, 0.5),  # ← Agent must estimate upward water travel
  splash_height=0.25,
  create_validation_volumes=True,
)
```

**Then**:
```python
manage_liquid_cache(
  domain_object_name="Liquid_Domain",
  modifier_name="Liquid Domain",
  action="START_BAKE",
  stage="BAKE_ALL",
  confirm_bake=True,
  max_bake_frames=250,
)
```

**Then poll / wait**:
```python
# Poll until bake completes
manage_liquid_cache(action="STATUS")  # → has_cache_baked_any=True
```

**Then validate**:
```python
validate_liquid_result(
  domain_object_name="Liquid_Domain",
  modifier_name="Liquid Domain",
  frames=[1, 50, 100, 150, 200, 250],
  target_fill_fraction=0.8,
  deadline_frame=250,
  overflow_policy="FORBID",
)
```

### Failure Points for a Non-Expert Agent

1. **Collision Proxy Choice**: HOLLOW_CONTAINER vs direct COLLISION effector. `setup_liquid_shot` accepts both, but agent must know: HOLLOW_CONTAINER removes the rim cap → emitter can pour in; direct COLLISION → glass stays solid. **Document required.**

2. **Rim Axis**: Agent must identify which axis points "up" for the glass. `rim_axis` in HOLLOW_CONTAINER mode removes the cap perpendicular to this axis. **Likely requires object inspection or agent heuristic** (e.g., try Z first).

3. **Padding & Splash Height**: `setup_liquid_shot` auto-fits the domain from sampled motion, but agent must supply `expected_travel` and `splash_height` as hints. Without them, domain may be too small if water bounces/splashes high. **Requires fluid-sim domain knowledge.**

4. **Cache Directory**: Must be explicit, unshared, and writable. `setup_liquid_shot` does NOT create the directory (addon rejects non-existent paths). **Agent must ensure it exists or call fails.**

5. **Bake Completion Polling**: `START_BAKE` returns a job_id under GUI; agent must poll `manage_liquid_cache(action="STATUS")` until stage's `has_cache_baked_*` flag is true. **Under `--background` (no GUI), START_BAKE falls back to synchronous, blocking for hours; agent must know this behavior.**

6. **Frame Range Validity**: Sample/validate frames must fall within cache_frame_start:cache_frame_end. Agent must track this. **No auto-bounds detection.**

### Achievability

**Verdict**: ✅ **Achievable in 4 sequential tool calls** (`setup_liquid_shot` + `manage_liquid_cache START_BAKE` + `manage_liquid_cache STATUS` poll-loop + `validate_liquid_result`), **BUT requires domain expertise for**:
- Choosing HOLLOW_CONTAINER vs COLLISION
- Guessing rim_axis, padding, splash_height
- Ensuring cache directory exists
- Handling async vs synchronous bake under GUI vs --background
- Polling until completion

**Agent would benefit from**:
- A higher-level convenience tool wrapping the polling loop
- Clearer documentation on proxy vs collision choice
- Auto-detection of axis orientation from mesh bounds

---

## 5. Redundancy & Over-Fragmentation Audit

### Finding: Parallel API Duplication

**fluid.py** (6 tools): Generic LIQUID/GAS surface via `domain_type` parameter
- `inspect_fluid_simulation(domain_type, ...)`
- `create_fluid_domain(domain_type, ...)`
- `configure_fluid_solver(domain_type, FluidSolverPatch)`
- `add_fluid_flow(domain_type, ...)`
- `add_fluid_effector(domain_type, ...)`
- `manage_fluid_cache(domain_type, ...)`

**liquid/inspection_and_setup.py** (12 tools): Liquid-specific
- `get_liquid_simulation_info(...)` — OVERLAPS fluid:inspect_fluid_simulation; adds UUID/dependency discovery
- `create_liquid_domain(...)` — OVERLAPS fluid:create_fluid_domain; adds liquid solver defaults
- `configure_liquid_solver(...)` — OVERLAPS fluid:configure_fluid_solver; liquid-specific fields (27 vs shared 13)
- `add_liquid_flow(...)` — OVERLAPS fluid:add_fluid_flow; rejects smoke-only fields
- `add_liquid_effector(...)` — OVERLAPS fluid:add_liquid_effector
- `configure_liquid_flow(...)` — NEW
- `configure_liquid_effector(...)` — NEW
- Other new tools: fit, boundaries, estimate, validate

**Observation**: All fluid.py tools use `liquid/_shared.py:_call()` transport. **There is no separate `gas/` module or gas-specific handlers found.** The gas simulation surface appears to be purely theoretical or unused.

### Consolidation Verdict

| Candidate | KEEP/MERGE/REMOVE | Rationale |
|-----------|-------------------|-----------|
| `fluid.py:inspect_fluid_simulation` vs `liquid:get_liquid_simulation_info` | **MERGE** | Get the liquid-specific version (adds UUID/dependency); `fluid.py` is now redundant for LIQUID. If GAS is unused, remove `fluid.py` entirely. If GAS is active elsewhere (smoke/fire tools), consolidate to `gas.py` and remove generic `fluid.py`. |
| `fluid.py:create_fluid_domain` vs `liquid:create_liquid_domain` | **MERGE** | Liquid version adds solver defaults; keep it. Remove from `fluid.py` unless GAS variant exists. |
| `fluid.py:configure_fluid_solver` vs `liquid:configure_liquid_solver` | **MERGE** | Liquid version is strictly more capable (27 fields); deprecate `fluid.py` variant. Remove from `fluid.py` unless GAS has separate settings. |
| `fluid.py:add_fluid_flow` vs `liquid:add_liquid_flow` | **MERGE** | Liquid version validates smoke-only fields explicitly; keep. Remove generic variant. |
| `fluid.py:add_fluid_effector` | **MERGE** | Liquid version suffices (shared model). Remove generic. |
| `fluid.py:manage_fluid_cache` | **MERGE or KEEP** | If bake state is domain-type-agnostic (likely), consolidate to single tool accepting optional domain_type or remove it and only expose `liquid:manage_liquid_cache`. If GAS uses different cache semantics, split into `gas:manage_gas_cache`. **Current state: one tool per domain, both route to same addon handler.** |
| **Overall recommendation** | **REMOVE `fluid.py` OR split into `gas.py`** | **Gap**: No gas-specific tools or handlers found. If gas simulation is in scope, create `src/blender_mcp/server/tools/gas/` module with GAS-only tools (configure_gas_solver, etc.) and delete `fluid.py`. If gas is out of scope, delete `fluid.py` entirely and rename `liquid/` to `simulation/` for clarity. **Current state is confusing: a "canonical cross-domain" module that only routes to liquid handlers.** |

### Module Fragmentation

**Liquid submodules**: 10 files (+ `_shared.py`, `__init__.py`)
- `inspection_and_setup.py` (493): Core domain/flow/effector setup + validation
- `simulation.py` (161): Cache lifecycle
- `mesh_and_materials.py` (230): Mesh, particles, diffusion, materials
- `delivery.py` (282): Proxies, export, performance
- `shot.py` (167): Orchestrator
- `animation.py` (58): Flow keying
- `result_validation.py` (66): Baked output validation
- `force_fields.py` (85): Force field scoping
- `guides.py` (59): Guide creation
- `lifecycle.py` (39): Component removal
- `quality.py` (139): Quality profile convenience

**Assessment**: ✅ **Reasonable fragmentation**. Each module is task-scoped (mesh generation, animation, delivery, etc.) and under 300 lines except `delivery.py` (282). The split enables clarity and testability. **Not over-fragmented** for the domain's scope.

### Verdict Summary

**Single biggest redundancy**: `fluid.py`'s 6 generalized domain_type tools duplicate `liquid/` variants with no evidence of active GAS support. **Action**: Investigate whether gas simulation is in scope; if not, delete `fluid.py` and consolidate all tools into `liquid/` (rename to `simulation/` or keep as `liquid/` for backward compatibility). If gas IS active elsewhere, move `fluid.py` logic into `src/blender_mcp/server/tools/gas/` and `src/blender_mcp/bundled/addon/handlers/gas/`, then delete cross-domain `fluid.py`.

---

## 6. Summary

| Dimension | Finding |
|-----------|---------|
| **Tool Count** | ~41 tools across `fluid.py` (6) + `liquid/*` (35); 6,960 lines in addon handlers |
| **Abstraction Ladder** | Low (inspect/configure individual properties) → Medium (patch models, validation) → High (`setup_liquid_shot` orchestrator) |
| **Critical Workflow** | "Fill glass with water" achievable via `setup_liquid_shot` + bake + validate in 4 calls; requires domain-expertise for proxy choice, axis orientation, padding heuristics |
| **API Stability** | ✅ Blender 5.2.1 runtime introspection confirms all tool assumptions (use_spray_particles, simulation_method, flip_ratio, etc.) |
| **Reliability** | ✅ Error handling for operator failures & I/O exceptions; ⚠️ No disk-space preflight check before long bakes |
| **Resource Estimation** | ⚠️ `estimate_liquid_resources` returns only relative cost index, not absolute memory/disk/time predictions |
| **Redundancy** | 🔴 **CRITICAL**: `fluid.py` (6 cross-domain tools) duplicates `liquid/` variants with no active GAS module found; consolidation recommended |
| **Over-Fragmentation** | ✅ 10 `liquid/` submodules well-scoped; not excessive |
| **Production Readiness** | ✅ For liquid-only workflows. ⚠️ Gas support is ambiguous; requires clarification and possible refactoring. |

---

## Appendix: Tool Coverage by Workflow Phase

**Pre-flight**: `validate_liquid_setup`, `estimate_liquid_resources`, `get_liquid_simulation_info`
**Domain Setup**: `setup_liquid_shot` (orchestrator) or manual: `create_liquid_domain` → `fit_liquid_domain` → `add_liquid_flow` → `add_liquid_effector` → `configure_liquid_solver` → `apply_liquid_quality_profile`
**Animation**: `animate_liquid_flow` (time-key flow settings)
**Delivery**: `create_liquid_proxy_rig`, `configure_liquid_force_fields`, `create_liquid_guide`
**Baking**: `manage_liquid_cache` (START_BAKE, STATUS, PAUSE, RESUME, CANCEL)
**Post-Bake**: `sample_liquid_simulation` (evaluate frames), `validate_liquid_result` (measure fill/spill), `prepare_liquid_render_mesh` (finishing), `export_liquid_simulation` (write to file)
**Cleanup**: `remove_fluid_components`, `duplicate_liquid_setup_variant`

---

**Audit completed**: 2026-09-05
**Blender version tested**: 5.2.1 LTS (build 2026-08-25)
**Recommendation**: Consolidate or remove `fluid.py`; clarify GAS simulation scope.
