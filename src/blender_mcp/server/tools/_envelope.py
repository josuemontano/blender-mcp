"""
Shared structured-result envelope for MCP tool return values.

Every tool that returns a dict (all tools except `get_viewport_screenshot` and
`get_sketchfab_model_preview`, which return an image plus this same envelope as a
second content item - see their docstrings) uses `ok()` to build it:

    {"ok": bool, "data": ..., "error": None, "warnings": [...], "changed_objects": [...],
     "changed_resources": [...]}

- `ok`: True unless the request reached Blender but produced no effect - for example an
  ND operator the user cancelled interactively (Esc). Check this before trusting
  `changed_objects`/`changed_resources`, which are empty whenever `ok` is False. A
  transport/validation failure never reaches this envelope; it raises `ToolError`.
- `data`: the tool-specific payload; see each tool's own Returns section for its shape.
- `error`: always None here; kept for shape symmetry with `ToolError`'s payload.
- `warnings`: non-fatal notices, e.g. that a topology-changing operation invalidated
  indices from an earlier `get_mesh_data` call, or that the operator was cancelled.
- `changed_objects`: names of Blender *objects* the call created, modified, or deleted.
  Never includes provider asset IDs, material/image/world names, or requested targets
  that turned out unchanged (e.g. a cancelled ND operator).
- `changed_resources`: names of non-object datablocks touched (materials, images,
  worlds, node groups, textures) - the counterpart to `changed_objects` for data that
  isn't a scene object.

Pagination fields (`list_scene_objects`, `get_mesh_data`, `search_polyhaven_assets`) live
inside `data`, not in this envelope: a `limit`/`offset` request, a total-count field
specific to that tool, `returned_count`, `truncated`, and `next_offset`. When `truncated`
is true, call again with `offset=next_offset` to continue.
"""

from typing import Any

STALE_INDEX_WARNING = (
    "This operation changed the mesh's topology. Vertex/edge/face indices from any get_mesh_data call made "
    "before this one are no longer reliable - call get_mesh_data again before reusing indices in further "
    "index-based edits."
)


def ok(
    data: Any = None,
    *,
    success: bool = True,
    warnings: list[str] | None = None,
    changed_objects: list[str] | None = None,
    changed_resources: list[str] | None = None,
) -> dict:
    merged_warnings = list(warnings or [])
    # The Blender addon surfaces non-fatal notices (e.g. that an undo checkpoint
    # could not be recorded) as a `warnings` list on its result. Lift them into
    # the envelope's own warnings so the client sees them, and drop the key from
    # `data` to avoid reporting the same notice twice. Every mutating tool passes
    # the addon result straight through as `data`, so this single point covers
    # them all.
    if isinstance(data, dict) and isinstance(data.get("warnings"), list):
        merged_warnings.extend(str(warning) for warning in data["warnings"])
        data = {key: value for key, value in data.items() if key != "warnings"}
    return {
        "ok": success,
        "data": data,
        "error": None,
        "warnings": merged_warnings,
        "changed_objects": changed_objects or [],
        "changed_resources": changed_resources or [],
    }
