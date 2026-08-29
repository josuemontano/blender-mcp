import requests

# Per-snapshot object cap for get_world_state_snapshot. Keep in sync with
# blender_mcp.trajectory.MAX_SNAPSHOT_OBJECTS.
MAX_SNAPSHOT_OBJECTS = 2000

# Selected-name cap for get_world_state_snapshot: select-all in a large scene
# would otherwise make `selected` the dominant field of both step snapshots.
# Keep in sync with blender_mcp.trajectory.MAX_SNAPSHOT_SELECTED.
MAX_SNAPSHOT_SELECTED = 200

RODIN_FREE_TRIAL_KEY = "vibecoding"

# Add User-Agent as required by Poly Haven API
REQ_HEADERS = requests.utils.default_headers()
REQ_HEADERS.update({"User-Agent": "blender-mcp"})

# region Manual edit capture
# Records what the human does in Blender while an MCP session is live.

MAX_EDIT_EVENTS = 256

# Operators that fire constantly during interactive work and carry no meaningful
# intent on their own.
_IGNORED_OPERATORS = frozenset(
    {
        "view3d.rotate",
        "view3d.move",
        "view3d.zoom",
        "view3d.dolly",
        "view3d.view_axis",
        "view3d.view_orbit",
        "view3d.view_pan",
        "view3d.smoothview",
        "view3d.cursor3d",
        "wm.tool_set_by_id",
        "wm.context_set_value",
        "screen.animation_step",
    }
)

# Operator properties holding filesystem paths. Never recorded.
_PATH_PROPERTY_NAMES = frozenset(
    {
        "filepath",
        "filename",
        "directory",
        "filepath_raw",
        "relpath",
    }
)
_PATH_PROPERTY_SUBSTRINGS = ("filepath", "filename", "directory", "_dir", "path")
MAX_OPERATOR_PROPERTY_CHARS = 200

# depsgraph_update_post fires on every scene update, many times per second
# during interactive drags.
EDIT_POLL_MIN_INTERVAL = 0.1

# endregion
