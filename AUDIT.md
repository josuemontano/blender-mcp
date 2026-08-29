# Blender MCP Production Audit — Current Rerun

Audit date: 2026-08-29

## Scope

This rerun reviews the current working tree against Blender 5.1 API expectations, production workflow requirements in `AGENTS.md`/`CLAUDE.md`, and the comparable-MCP baseline used by the earlier audit. It covers correctness, reliability, API design, workflow coverage, and documentation presented to agents. Code style is excluded.

The MCP currently registers **45 tools**:

| Area | Tools |
|---|---:|
| Core/status | 2 |
| Arbitrary execution | 1 |
| Mesh/general cleanup | 13 |
| Model/modifier | 7 |
| ND | 10 |
| Poly Haven | 4 |
| Sketchfab | 3 |
| Viewport/inspection | 5 |

Per request, **no tests or quality checks were run for this audit**. Findings are based on current source inspection; Blender-runtime verification is still required for operator context, evaluated geometry, imports, undo behavior, and GPU capture.

## Status changes since the previous audit

| Previous finding | Current status | Assessment |
|---|---|---|
| Quaternion/axis-angle failure in `copy_object_transform` | **Solved** | Rotation is returned in the object's native representation, with separate decomposed world-space fields. |
| Incorrect radial-array pivot composition | **Solved in code; Blender verification pending** | The helper now builds `T(pivot) @ R @ T(-pivot)` and derives the offset object's world matrix from the source object. This is mathematically appropriate, but must still be exercised in Blender with parented, rotated, and non-uniformly scaled objects. |
| Object inspection omitted modifiers and mishandled non-Euler rotation | **Solved** | `get_object_info` now returns rotation according to `rotation_mode` and includes basic modifier entries. |
| Local/base-mesh ambiguity in `get_mesh_data` | **Mostly solved** | The tool now states that coordinates and normals are base-mesh object-space data. Its suggested world conversion is incomplete because `get_object_info` does not return `matrix_world`. |
| ND cancellation reported success and stale active object | **Mostly solved** | Cancelled ND operations now return `ok:false`, no changed objects, and `nd_single_vertex` detects creation by object diff. The failure envelope and actual-change reporting remain incomplete. |
| No mutation checkpoint or rollback mechanism | **Solved** | The transaction now tracks datablocks by `session_uid` (stable across renames) instead of name, captures and restores each touched existing object's name/transform/parent/material slots/added modifiers (and a geometry backup for mesh-editing commands), coerces handler-returned failure shapes into exceptions so partial provider mutations roll back, and reports rather than suppresses an unavailable undo checkpoint. See the resolved transaction finding below for residual, documented limitations. |
| Socket framing changes were uncommitted | **Solved** | NDJSON framing and request IDs are committed. The per-frame size-limit defect is now fixed. |

Previously solved items remain solved: failure-shape normalization; main-thread command dispatch; mode/selection/active-object restoration for covered mutation tools; Edit Mode synchronization; mesh index validation; stale-topology warnings on direct mesh tools; ND operator/context/modal checks; confirmation for scene-wide ND cleanup; removal of Hyper3D/Hunyuan; evaluated modifier counts/bounds; socket Unicode/framing/correlation; provider path checks; and GPU off-screen screenshot fallback.

## Pending implementation findings

### Critical

| Finding | Status | Recommendation |
|---|---|---|
| Arbitrary Python execution is exposed through an unauthenticated, auto-started local socket | **Pending** | Remove `execute_blender_code`/`execute_code` from the default production capability set. Require an explicit opt-in and a per-session authentication secret before accepting commands. Default auto-start to false and expose the existing auto-start property in the Blender UI. Loopback binding does not protect Blender from another local process. Relevant files: [`execute.py`](src/blender_mcp/server/tools/execute.py), [`server_core.py`](src/blender_mcp/bundled/addon/server_core.py), [`__init__.py`](src/blender_mcp/bundled/addon/__init__.py), [`ui.py`](src/blender_mcp/bundled/addon/ui.py). |
| Mutation rollback can delete pre-existing renamed datablocks and does not restore existing state | **Resolved** | `_snapshot_ids`/`_new_datablocks` in [`transaction.py`](src/blender_mcp/bundled/addon/transaction.py) now diff by each datablock's `session_uid` (documented stable across renames/reallocation) instead of `.name`, so a pre-existing datablock renamed mid-request is never mistaken for new and deleted, and a new datablock reusing a freed name is still recognised as new. New [`object_state.py`](src/blender_mcp/bundled/addon/object_state.py) captures each touched existing object's name, data name, local transform, parent, material-slot assignments, and pre-existing modifier set before a mutating request runs, and restores them (plus, for geometry-editing commands, a swapped-back mesh backup) on failure. `server_core._run_handler` resolves an operation's target objects from its params, converts a handler-returned failure shape (`{"error": ...}`, `succeed: False`) into a raised `HandlerReportedError` inside the transaction — never a legitimate `{"cancelled": True}` outcome — so partial provider mutations roll back instead of committing with a checkpoint. The undo checkpoint step reports (via a `warnings` entry lifted into the MCP envelope by `_envelope.ok()`) rather than silently suppresses background-mode/global-undo-disabled/failed-push cases. Residual, documented limitations: deleted pre-existing datablocks are not resurrected (stays gated behind confirmation), applied-modifier and `execute_code` side effects remain outside capture, and a mesh restored from its geometry backup gets a new `session_uid`. Covered by `tests/test_mutation_transaction.py` and `tests/test_envelope_warnings.py`. |

### High

| Finding | Status | Recommendation |
|---|---|---|
| Provider HTTP blocks Blender's main thread; MCP `async` tools block the MCP event loop | **Pending** | Perform HTTP, archive validation, and file writes in bounded worker tasks; queue only `bpy` mutations onto Blender's main thread. Run blocking socket calls from MCP tools with `asyncio.to_thread` or replace them with an async transport. Add cancellation/progress and do not drain an unlimited command backlog in one Blender timer callback. |
| Poly Haven requests have no timeouts or download bounds | **Pending** | Add connect/read timeouts, streamed downloads, maximum bytes, `raise_for_status`, content/type validation, and cleanup in `finally` for every request. Replace private `tempfile._cleanup()` with explicit cleanup. |
| Poly Haven HDRI setup mutates the wrong world and destructively replaces nodes | **Pending** | Operate on `bpy.context.scene.world`, creating one only when that scene has none. Make replacement explicit, preserve or report the prior world/node setup, pack the image or keep a stable source file, and report the changed world/image resources. `bpy.data.worlds[0]` is not necessarily the active scene world. |
| Poly Haven material application silently removes all existing material slots | **Pending** | Require an explicit replacement policy or target slot. Reuse the material created during texture import instead of rebuilding a duplicate graph, remove the duplicate node-building passes, and report the material and images changed. Do not call this a simple assignment while replacing the object's complete material configuration. |
| Poly Haven model import and `changed_objects` are inaccurate | **Pending** | Diff `bpy.data.objects` before/after import, validate the import operator's `FINISHED` result, and return the actual imported object names. Do not report an asset ID as an object name. Roll back imported objects and dependencies on failure. |
| Sketchfab downloads/imports lack production resource limits and reliable import detection | **Pending** | Validate `target_size` as finite and greater than zero; stream with compressed/uncompressed limits; reject absolute, drive-qualified, UNC, traversal, and unsafe archive members; find nested GLTF files deterministically; clean the temporary directory in `finally`; validate the import result; detect imports by before/after object diff; and roll back a failed normalization. |
| Sketchfab results omit required provenance | **Pending** | Preserve model UID, canonical URL, author, license, attribution text, and source metadata in search/import results and, where practical, on imported collections/objects. Preview currently discards author/model metadata when converting the response to an `Image`. |
| Numeric and structural input validation remains inconsistent | **Pending** | Validate finite vectors and meaningful ranges before mutation: positive voxel/texture/screenshot size, valid subdivision/cut/array counts, supported texture type, non-degenerate dimensions, and provider count limits. Validate complete object lists before changing any member. Validate raw socket commands as objects with string `id`/`type` and mapping `params`. |
| Applied modifier tools omit topology-invalidation warnings | **Pending** | When `apply=True`, `add_subdivision_surface_modifier`, `add_displace_modifier`, `model_mirror`, `model_array`, and `model_radial_array` rebuild topology just as `mesh_solidify` does. Return the same stale-index warning. `add_subdivision_surface_modifier` must also disclose or remove its unconditional `shade_smooth` mutation, which changes the base mesh even when the modifier remains live. |

### Medium

| Finding | Status | Recommendation |
|---|---|---|
| Transaction classification creates undo noise and is denylist-based | **Pending** | Explicitly classify mutating commands rather than treating every unknown command as mutating. `get_addon_info` currently receives a transaction/undo checkpoint, while viewport and ND display/selection operations also create misleading undo entries. Cancellation should not create a successful checkpoint. |
| Socket message limit does not limit complete frames | **Solved** | Both `handle_client` (addon) and `receive_full_response` (server) now check the byte length of each frame/line as soon as it is split off, rejecting it and dropping the connection if it exceeds `_MAX_MESSAGE_BYTES` - independent of the existing unterminated-remainder check. |
| Socket queue and timer drain are unbounded | **Pending** | Bound per-client/global queued commands and process only a fixed count or time budget per timer tick. Otherwise a local client can exhaust memory or freeze Blender by continuously filling a queue that is drained to completion. Return structured errors for malformed and rejected requests instead of silently dropping them. |
| Logs expose arbitrary client code and potentially large payloads | **Pending** | Stop logging full `params`; log command type, request ID, safe object identifiers, duration, and byte counts. `execute_blender_code` source must never be written to routine logs or tracebacks. |
| Success/error envelopes are inconsistent | **Pending** | Document and enforce one contract. Most successful tools return `{ok,data,error,warnings,changed_objects}`, image tools return raw `Image`, transport failures raise `ToolError`, and ND cancellation returns `ok:false` with `error:null`. Give non-success results an actionable error/status code, and define how binary tools report metadata. Consider `changed_resources` because worlds, materials, images, modifiers, and deleted objects cannot be represented accurately by `changed_objects`. |
| ND “real changed objects” are still optimistic | **Partially solved** | Cancellation is handled, but successful operators commonly report all targets even if ND changed none. `nd_clean_utils` omits objects whose modifiers changed unless the objects were deleted. Diff relevant object/modifier/material state and report actual changed names/resources. |
| Dynamic capability visibility and caching remain inconsistent | **Pending** | FastMCP exposes Poly Haven, Sketchfab, and ND tools even when disabled. The connection caches handshake capabilities, so enabling an integration can remain blocked until a forced handshake/reconnect. Filter or annotate the public tool list from current capabilities and refresh capabilities after Blender settings change. |
| Inspection is insufficient for a production preflight | **Pending** | `list_scene_objects` rounds local locations to two decimals and omits active object, selected objects, current mode, collection membership, parent, scene units, visibility flags, and stable ordering. `get_object_info` omits `matrix_world`, dimensions, parent, collections, data name, and detailed modifier settings. Return enough focused state for the inspect-first workflow required by repository guidance. |
| `get_mesh_data` documentation references unavailable `matrix_world` | **Pending documentation/contract defect** | Either return `matrix_world` from `get_object_info` or remove the instruction that agents can obtain it there. If world-space normals are added, use the inverse-transpose normal matrix rather than the position transform. |
| Provider search pagination is incomplete | **Pending** | Replace Poly Haven's hard-coded first 20 entries with deterministic `limit`/`offset` pagination and return `truncated`/`next_offset`. Preserve Sketchfab continuation data and accept a cursor/page input. Rename `search_polyhaven_assets` to `list_polyhaven_assets`, with a compatibility alias if required. |
| Screenshot temporary file handling is not failure-safe | **Pending** | Use a unique per-request temporary file and remove it in `finally`. Validate `max_size`. Return or otherwise preserve capture method and dimensions, which are currently discarded when the tool returns only an `Image`. |
| Displacement contract remains overly broad | **Pending** | Restrict `texture_type` to verified Blender 5.1 legacy procedural texture types that support the configured property, validate scale, document that this is the legacy Texture datablock API rather than shader nodes, and return the generated texture and optional subdivision modifier names. |
| Helper/resource reporting is incomplete | **Pending** | A live radial array creates a helper empty but neither returns its name nor includes it in changed resources. Displacement can create a texture and two modifiers but reports only the final modifier. Return every retained helper/datablock so agents can inspect and clean them safely. |

### Low / API clarity

| Finding | Status | Recommendation |
|---|---|---|
| Some names still describe the mechanism inaccurately | **Pending** | Rename `viewport_overlay_toggle` to `set_viewport_overlay` because it is idempotent. Rename `download_sketchfab_model` to `import_sketchfab_model` because it downloads, imports, and rescales the scene. Consider modifier-oriented names for `model_mirror` and `model_array`. Version any breaking aliases. |
| `execute_blender_code` return behavior is undocumented | **Pending** | State that only captured stdout is returned, agents must `print()` values, namespaces do not persist between calls, no execution timeout exists, and filesystem/preferences/external side effects are outside rollback. If retained, return an explicit execution result and truncated stdout/stderr separately. |

## Production workflow coverage gaps

These are gaps in the workflows currently advertised or required by the repository's own production guidance. They explain why agents still fall back to unrestricted Python; they are not a proposal for unrelated feature expansion.

| Workflow requirement | Current gap and impact |
|---|---|
| Inspect before editing | No single inspection path exposes selection, active object, mode, scene units, hierarchy, collections, complete transforms, or evaluated dimensions. Agents cannot establish the required production baseline without arbitrary code. |
| Deterministic object organization | There are no dedicated operations for collection creation/membership, general rename, parenting, duplication/instancing, or safe object removal. Imported helpers and assets cannot be organized or cleaned without arbitrary code. |
| Intentional transforms | Apart from copying another object's transform and creation-time primitive placement, there is no validated general transform setter. Agents cannot reliably place or orient imported/existing objects without code. |
| Materials | The only assignment workflow is Poly Haven-specific and destructive. There is no dedicated basic material/property/slot workflow despite the prompt explicitly recommending arbitrary code for basic color. |
| Modifier lifecycle | Tools create selected modifiers, but agents cannot inspect their settings deeply, reorder them, update them, remove them, or explicitly apply an existing named modifier. This weakens non-destructive iteration and recovery. |
| Mesh production data | Inspection omits UVs, color attributes, generic attributes, vertex groups/weights, shape keys, material assignment per workflow, and evaluated mesh data. Agents cannot verify texture readiness or deformation/export state. |
| Camera, lighting, render, save/export | README examples advertise studio lighting, camera aiming, and downstream scene use, but no dedicated tools cover camera/light configuration, render settings, saving, export, or explicit output paths. Those workflows depend entirely on arbitrary code. |
| Verification | Screenshots verify appearance only; they cannot establish topology, modifier, hierarchy, UV, units, or export correctness. The current prompt overuses screenshot/list calls without a structured completion checklist based on available data. |
| Long-running work | No progress, cancellation, job ID, retry, or resumable-download contract exists. One long provider operation blocks Blender and the MCP caller until the socket timeout. |

## Missing or misleading documentation for agents

### Tool descriptions and response contracts

1. There is no agent-facing tool reference or shared explanation of the result envelope. Agents are not told to inspect `ok`, `warnings`, `changed_objects`, pagination fields, or ND cancellation before continuing.
2. Many public tool `Returns` sections still say only “Result produced by the operation” or `dict`, which does not document exact fields, units, coordinate spaces, retained helpers, destructive effects, or next safe action.
3. Applied modifier tools do not warn that `apply=True` is irreversible and invalidates previously retrieved mesh indices. Cleanup tools do not mention the scope of data removed or the available Blender undo checkpoint.
4. `changed_objects` semantics are undefined: it can name modified objects, deleted cutters, requested targets that may be unchanged, or—incorrectly for Poly Haven—an asset ID. Non-object resources are invisible.
5. Image-returning tools do not follow or explain the standard envelope. Sketchfab preview silently drops the author/model information that its handler retrieved; viewport capture drops dimensions and capture method.

### Agent strategy prompt

The single prompt in [`prompts.py`](src/blender_mcp/server/prompts.py) is asset-centric and contains several production problems:

- It does not begin with `get_addon_status`, so an agent may use schemas unsupported by an outdated addon.
- It says to check every object's world bounding box, but `list_scene_objects` does not return bounding boxes and the process is not paginated/explained.
- It recommends screenshots as the primary verification method without explaining that screenshots do not validate topology, units, hierarchy, modifiers, or materials.
- It does not teach the safe index workflow, local/world-space rules, `apply=True` consequences, destructive cleanup, ND cancellation, response-envelope handling, or undo limitations.
- Its fallback rule is contradictory: the “no dedicated tool” example names primitive and mesh operations that do have dedicated tools.
- It explicitly routes basic materials/colors to arbitrary Python, exposing the implementation gap without any safety template or restricted-code guidance.
- It does not instruct agents to stop on `ok:false`, warnings, stale indices, partial provider results, or an addon capability mismatch.

Replace it with a concise staged workflow: verify addon/capabilities; inspect scene and target object; choose dedicated non-destructive tools; re-query after topology changes; verify structured state plus screenshot; report changed resources and limitations. Provider-specific guidance should be conditional rather than dominating every modeling request.

### README and operator UI

- README claims “create, delete and modify shapes,” general material creation, studio lighting, camera aiming, and similar workflows that have no dedicated tools. Clarify that these require arbitrary execution or narrow the claims.
- The security section understates the impact of unauthenticated arbitrary execution. “Save first” is not an adequate production control; document the trust boundary, local-process risk, auto-start behavior, and lack of authentication.
- README does not document the response envelope, capability handshake, maximum message size, 180-second socket timeout, undo/rollback limitations, provider download limits, or agent-safe operation sequence.
- The registered `blendermcp_auto_start_server` property is not exposed in the panel, so users cannot discover or change the security-relevant default through the UI.
- UI labels still say “Claude” in some operator metadata although the server is a general MCP endpoint.

## Removal and consolidation conclusion

`execute_blender_code` is the only current tool that should be removed from the default production surface; retain it only as a separately enabled development capability.

No other current tool is inherently redundant. Native mesh operations and ND operations serve destructive versus non-destructive workflows. The Poly Haven import/apply separation is useful, but the material implementation should reuse a single imported material rather than building duplicate graphs. The two image-returning tools should share a documented binary-result convention.

## Overall assessment

The current implementation is materially stronger than the earlier version: structured modeling coverage, transform handling, inspection, main-thread dispatch, and ND behavior have improved. Compared with code-execution-centric Blender MCPs, its dedicated tools provide a better foundation for reliable agent use.

It is **not yet production-safe**. The release blockers are unauthenticated arbitrary execution, unsafe/incomplete rollback, blocking and unbounded provider pipelines, destructive Poly Haven behavior, incomplete Sketchfab validation/provenance, and insufficient preflight/agent documentation. Address those before treating undo checkpoints or provider imports as recoverable production operations.
