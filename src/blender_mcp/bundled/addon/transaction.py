import contextlib

import bpy

# Every bpy.data collection a mutating handler could plausibly create
# datablocks in - modifier/texture/material creation (materials, textures,
# node_groups), imports (objects, meshes, armatures, actions, images, ...),
# and model helpers (objects, collections). Broad on purpose so new handlers
# get rollback coverage for free without updating this list.
_TRACKED_COLLECTIONS = (
    "objects",
    "meshes",
    "curves",
    "materials",
    "textures",
    "images",
    "node_groups",
    "worlds",
    "actions",
    "armatures",
    "cameras",
    "lights",
    "collections",
)


def _snapshot_names():
    """
    Capture the current name set of every tracked bpy.data collection.

    Returns:
        dict[str, set[str]]: collection name -> set of datablock names present right now.

    """
    return {coll_name: {db.name for db in getattr(bpy.data, coll_name)} for coll_name in _TRACKED_COLLECTIONS}


def _new_datablocks(before):
    """
    Diff the current bpy.data state against an earlier snapshot.

    Args:
        before: A snapshot previously returned by _snapshot_names().

    Returns:
        list[tuple[str, ID]]: (collection name, datablock) pairs created since the snapshot,
        in creation/iteration order.

    """
    created = []
    for coll_name in _TRACKED_COLLECTIONS:
        before_names = before.get(coll_name, set())
        for db in getattr(bpy.data, coll_name):
            if db.name not in before_names:
                created.append((coll_name, db))
    return created


def _remove_datablocks(entries) -> None:
    """
    Best-effort removal of newly-created datablocks after a failed mutation.

    Objects are removed first (and in reverse creation order) since a later
    object could reference an earlier one; everything else follows, also in
    reverse. Each removal is isolated so one failure doesn't stop the rest
    of the cleanup from running.

    Args:
        entries: (collection name, datablock) pairs, as returned by _new_datablocks().

    """
    objects = [db for coll_name, db in entries if coll_name == "objects"]
    others = [(coll_name, db) for coll_name, db in entries if coll_name != "objects"]

    for obj in reversed(objects):
        with contextlib.suppress(Exception):
            bpy.data.objects.remove(obj, do_unlink=True)

    for coll_name, db in reversed(others):
        with contextlib.suppress(Exception):
            getattr(bpy.data, coll_name).remove(db, do_unlink=True)


def _push_undo_checkpoint(message) -> None:
    """
    Push one named undo step, ignoring failures - this is a convenience
    checkpoint, not the rollback mechanism, and must never mask the
    request's real result.

    Args:
        message: Label shown in Blender's Undo History for this step.

    """
    with contextlib.suppress(Exception):
        bpy.ops.ed.undo_push(message=message)


@contextlib.contextmanager
def mutation_transaction(cmd_type):
    """
    Wrap one mutating MCP request: roll back newly-created datablocks on
    failure, and leave exactly one explicit, named undo checkpoint on success.

    Rollback deliberately removes tracked datablocks itself rather than
    calling bpy.ops.ed.undo() - Blender's undo stack has a finite depth
    (preferences.edit.undo_steps, default 32), and a handler that triggers
    many internal operator calls (e.g. a large batch op) could evict our own
    checkpoint before we'd get a chance to use it. Explicit tracking has no
    such failure mode; global undo is used here only for the one
    after-the-fact checkpoint, never as the rollback mechanism itself.

    Args:
        cmd_type: The MCP command type, used to label the undo checkpoint.

    """
    before = _snapshot_names()
    try:
        yield
    except Exception:
        _remove_datablocks(_new_datablocks(before))
        raise
    else:
        _push_undo_checkpoint(f"MCP: {cmd_type}")
