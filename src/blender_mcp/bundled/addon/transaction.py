import contextlib

import bpy

from .object_state import (
    backup_datablock_ids,
    capture_object_states,
    discard_backups,
    restore_object_states,
)

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
    "pointclouds",
    "grease_pencils",
)


def _snapshot_ids():
    """
    Capture the session_uid of every datablock in every tracked collection.

    session_uid (not name) is the identity key: it is documented stable across
    renames and internal reallocations, so a datablock renamed during a failed
    request is still recognised as pre-existing and never mistaken for a new
    one. A datablock without a session_uid (should not happen on Blender 5.1)
    is skipped rather than crashing the snapshot.

    Returns:
        dict[str, set[int]]: collection name -> set of session_uids present now.

    """
    snapshot = {}
    for coll_name in _TRACKED_COLLECTIONS:
        ids = set()
        for db in getattr(bpy.data, coll_name, ()):
            uid = getattr(db, "session_uid", None)
            if uid is not None:
                ids.add(uid)
        snapshot[coll_name] = ids
    return snapshot


def _new_datablocks(before, exclude_ids: "frozenset[int] | set[int]" = frozenset()):
    """
    Diff the current bpy.data state against an earlier session_uid snapshot.

    Args:
        before: A snapshot previously returned by _snapshot_ids().
        exclude_ids: session_uids to treat as not-new even when absent from the
            snapshot - used for the transaction's own geometry-backup meshes,
            which are rollback scaffolding rather than handler output.

    Returns:
        list[tuple[str, ID]]: (collection name, datablock) pairs created since
        the snapshot, in creation/iteration order.

    """
    created = []
    for coll_name in _TRACKED_COLLECTIONS:
        before_ids = before.get(coll_name, set())
        for db in getattr(bpy.data, coll_name, ()):
            uid = getattr(db, "session_uid", None)
            if uid is None or uid in before_ids or uid in exclude_ids:
                continue
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


def _undo_unavailable_reason():
    """
    Report why a global-undo checkpoint cannot be recorded, if it cannot.

    Undo is a no-op in background mode and when the user disabled global undo
    in preferences (Blender docs: use_global_undo). Both are documented,
    reliable signals - unlike undo_push's undocumented return value.

    Returns:
        str | None: A short reason, or None when undo should be available.

    """
    if getattr(bpy.app, "background", False):
        return "Blender is running in background mode"
    with contextlib.suppress(Exception):
        if not bpy.context.preferences.edit.use_global_undo:
            return "global undo is disabled in Blender preferences"
    return None


def _push_undo_checkpoint(message):
    """
    Push one named undo step, reporting (never silently suppressing) when undo
    protection is unavailable - a caller-visible warning, not the rollback
    mechanism itself.

    Args:
        message: Label shown in Blender's Undo History for this step.

    Returns:
        str | None: A warning to surface to the client when the checkpoint
        could not be created, or None on success.

    """
    reason = _undo_unavailable_reason()
    if reason is not None:
        print(f"BlenderMCP: undo checkpoint skipped - {reason}")
        return (
            f"Undo checkpoint unavailable ({reason}): this operation is not "
            "individually undoable via Blender's Undo History."
        )
    try:
        result = bpy.ops.ed.undo_push(message=message)
    except Exception as e:
        print(f"BlenderMCP: undo_push failed - {e!s}")
        return "Undo checkpoint could not be created: this operation is not individually undoable via Blender's Undo History."
    # undo_push's return is undocumented ("internal use only"); only an explicit
    # CANCELLED is a reliable failure signal. A None/other return is treated as
    # success rather than risk a false "unavailable" warning on every call.
    if isinstance(result, (set, frozenset)) and "CANCELLED" in result:
        print(f"BlenderMCP: undo_push returned {result}")
        return "Undo checkpoint could not be created: this operation is not individually undoable via Blender's Undo History."
    return None


class Transaction:
    """
    One mutating MCP request's rollback bookkeeping.

    Holds the pre-mutation session_uid snapshot and the captured state of the
    objects the request touches, so a failure can both remove datablocks the
    request created and restore the existing objects it changed.
    """

    def __init__(self, cmd_type) -> None:
        self.cmd_type = cmd_type
        self._before_ids = {}
        self._states = []
        self._backup_ids: frozenset[int] | set[int] = frozenset()
        self.committed = False

    def begin(self, targets, capture_geometry) -> None:
        self._before_ids = _snapshot_ids()
        self._states = capture_object_states(targets, capture_geometry=capture_geometry)
        self._backup_ids = backup_datablock_ids(self._states)

    def rollback(self) -> None:
        # Restore existing objects first: a slot/parent may reference a
        # datablock this request created, and we want the reference put back to
        # its pre-request target before that created datablock is removed.
        restore_object_states(self._states)
        _remove_datablocks(_new_datablocks(self._before_ids, exclude_ids=self._backup_ids))
        # Any backups not consumed by a geometry restore are pure scaffolding.
        discard_backups(self._states)

    def commit(self):
        """
        Finalise a successful request: drop rollback scaffolding and leave one
        named undo checkpoint.

        Returns:
            str | None: A warning to surface when undo protection was
            unavailable, else None. Idempotent - a second call is a no-op.

        """
        if self.committed:
            return None
        self.committed = True
        discard_backups(self._states)
        return _push_undo_checkpoint(f"MCP: {self.cmd_type}")


@contextlib.contextmanager
def mutation_transaction(cmd_type, targets=(), capture_geometry=False):
    """
    Wrap one mutating MCP request with identity-based rollback.

    Guarantees on failure (any exception leaving the block):
    - every datablock the request *created* is removed, identified by
      session_uid so a renamed pre-existing datablock is never deleted; and
    - the captured state of each target object is restored: name, data name,
      local transform, parent, material-slot assignments, modifiers added
      during the request, and (for geometry-editing commands) the mesh itself.

    On success it leaves exactly one named undo checkpoint, returning a warning
    via `Transaction.commit()` when that checkpoint could not be created.

    Explicitly NOT guaranteed (documented limitations, not silent gaps):
    deleted pre-existing datablocks are not resurrected; applied modifiers
    (e.g. nd_apply_modifiers) are irreversible; execute_code side effects and
    any object state outside the captured fields are not restored. Rollback
    removes tracked datablocks directly rather than calling bpy.ops.ed.undo():
    the undo stack is bounded (undo_steps, default 32) and evictable, is a
    no-op in background mode / when global undo is off, and is documented
    "internal use only" - none of which is a sound basis for correctness.

    Args:
        cmd_type: The MCP command type, used to label the undo checkpoint.
        targets: Existing objects the request will touch, whose state is
            captured for restore-on-failure.
        capture_geometry: When True, back up each target's mesh so a failed
            geometry edit can be reverted.

    Yields:
        Transaction: call .commit() after the handler succeeds to push the
        checkpoint and collect any undo-unavailability warning.

    """
    txn = Transaction(cmd_type)
    txn.begin(targets, capture_geometry)
    try:
        yield txn
    except Exception:
        txn.rollback()
        raise
    else:
        # A caller that didn't commit explicitly (so it could merge the
        # warning into its result) still gets a checkpoint here.
        txn.commit()
