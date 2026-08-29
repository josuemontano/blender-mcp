"""Core/meta tools: addon status, telemetry opt-out, trajectory feedback."""

import json
import logging

from mcp.server.fastmcp import Context

from ...addon_manager import EXPECTED_ADDON_PROTOCOL_VERSION
from ...consent_prompt import maybe_prompt_for_consent
from ...telemetry import get_telemetry
from ..app import mcp
from ..connection import force_addon_handshake, get_blender_connection

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
async def get_addon_status(ctx: Context, user_prompt: str = "") -> str:
    """
    Check whether the connected Blender addon matches this MCP server version.

    If outdated, tells the user how to update via `blender-mcp install-addon`
    (then restart or re-enable the addon in Blender).

    `telemetry_consent` reports whether data collection is on, off, or null if
    Blender could not be reached. Use it to answer telemetry status questions.
    """
    try:
        blender = get_blender_connection()
        result = force_addon_handshake(blender)
        if result is None:
            return "Could not determine addon status." + await maybe_prompt_for_consent(
                ctx
            )
        payload = {
            "up_to_date": result.up_to_date,
            "protocol_version": result.protocol_version,
            "expected_protocol_version": EXPECTED_ADDON_PROTOCOL_VERSION,
            "addon_version": result.addon_version,
            "capabilities": result.capabilities,
            "blender_version": result.blender_version,
            "source": result.source,
            "warning": result.warning,
            "telemetry_consent": get_telemetry().check_user_consent(),
            "update_command": "blender-mcp install-addon",
            "after_install": (
                "If the addon file was updated: in Blender, Preferences → Add-ons → "
                "disable/enable 'Interface: Blender MCP', or restart Blender, then Start MCP Server."
            ),
        }
        return json.dumps(payload, indent=2) + await maybe_prompt_for_consent(ctx)
    except Exception as e:
        return f"Error checking addon status: {e}"


@mcp.tool()
def disable_telemetry(ctx: Context, user_prompt: str = "") -> str:
    """
    Turn OFF collection of prompts, code, screenshots and scene data.

    Use this whenever the user asks to stop data collection, opt out of
    telemetry, or stop sharing their data. Takes effect immediately.

    This tool can only turn collection OFF. Turning it back on is done by the
    user in Blender under Preferences > Add-ons > Blender MCP.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_telemetry_consent", {"consent": False})
        if "error" in result:
            return f"Could not turn off data collection: {result['error']}"
        get_telemetry().invalidate_consent_cache()
        return (
            "Data collection is now OFF. Prompts, code, screenshots and scene "
            "data are no longer collected. Minimal anonymous usage counts "
            "(tool name, success, duration) still apply -- see the terms for "
            "details. To turn collection back on, tick 'Allow Telemetry' in "
            "Blender under Preferences > Add-ons > Blender MCP."
        )
    except Exception as e:
        return f"Error turning off data collection: {e}"


@mcp.tool()
def record_trajectory_feedback(
    ctx: Context,
    feedback: str,
    correction_text: str = None,
    step_index: int = None,
    user_prompt: str = "",
) -> str:
    """
    Record evaluation feedback for a captured trajectory step.

    Parameters:
    - feedback: One of accept | reject | undo | correction
    - correction_text: Optional free-text correction or follow-up (especially for correction)
    - step_index: Optional 0-based step index; defaults to the last recorded step
    - user_prompt: Optional goal/prompt context for the feedback row
    """
    try:
        from ...trajectory import get_trajectory_recorder

        allowed = {"accept", "reject", "undo", "correction"}
        if feedback not in allowed:
            return f"Error: feedback must be one of {sorted(allowed)}"

        recorder = get_trajectory_recorder()
        ok = recorder.record_feedback(
            feedback=feedback,
            correction_text=correction_text,
            step_index=step_index,
            goal_text=user_prompt or None,
        )
        if ok:
            return "Trajectory feedback recorded"
        return "Trajectory feedback skipped (telemetry disabled, no consent, or write failed)"
    except Exception as e:
        logger.debug(f"record_trajectory_feedback failed: {e}")
        return f"Trajectory feedback skipped: {e}"
