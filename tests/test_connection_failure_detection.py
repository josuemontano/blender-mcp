"""
Tests that a Blender-side failure never comes back as a successful result.

Many addon handlers catch their own exceptions and return an ad-hoc failure
shape ({"error": ...}, {"succeed": False, "error": ...}, or a bare
"Error: ..." string) instead of raising. The addon's own dispatcher then
wraps that as {"status": "success", "result": <value>}, since only a raised
exception produces {"status": "error", ...}. `_ad_hoc_failure_message` and
`_send_command_locked` are the safety net that catches this on the MCP
server side regardless of which addon version is installed.
"""

from __future__ import annotations

import json

import pytest

from blender_mcp.server.connection import BlenderConnection, _ad_hoc_failure_message


class _FakeSocket:
    """Minimal stand-in so _send_command_locked never touches a real socket."""

    def sendall(self, data: bytes) -> None:
        pass

    def settimeout(self, value: float) -> None:
        pass


def _connection_returning(payload: dict, monkeypatch) -> BlenderConnection:
    conn = BlenderConnection(host="localhost", port=0)
    conn.sock = _FakeSocket()
    monkeypatch.setattr(conn, "receive_full_response", lambda sock: json.dumps(payload).encode("utf-8"))
    return conn


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"error": "SecretId or SecretKey is not given"}, "SecretId or SecretKey is not given"),
        ({"succeed": False, "error": "No mesh objects imported from GLB"}, "No mesh objects imported from GLB"),
        ({"succeed": False}, "{'succeed': False}"),
        ("Error: Unknown Hyper3D Rodin mode!", "Error: Unknown Hyper3D Rodin mode!"),
    ],
)
def test_ad_hoc_failure_message_detects_known_failure_shapes(result, expected) -> None:
    assert _ad_hoc_failure_message(result) == expected


@pytest.mark.parametrize(
    "result",
    [
        {"succeed": True, "name": "Cube", "type": "MESH"},
        {"enabled": True, "message": "Hyper3D Rodin integration is enabled and ready to use."},
        {"name": "Scene", "object_count": 3, "objects": []},
        {"pong": True},
        None,
        "job_12345",
        [1, 2, 3],
    ],
)
def test_ad_hoc_failure_message_leaves_real_success_alone(result) -> None:
    assert _ad_hoc_failure_message(result) is None


def test_send_command_raises_on_nested_error_dict(monkeypatch) -> None:
    conn = _connection_returning(
        {"status": "success", "result": {"error": "SecretId or SecretKey is not given"}}, monkeypatch
    )

    with pytest.raises(Exception, match="SecretId or SecretKey is not given"):
        conn._send_command_locked("generate_hunyuan3d_model")

    # A clean operation failure is not a transport problem - the socket must survive it.
    assert conn.sock is not None


def test_send_command_raises_on_succeed_false(monkeypatch) -> None:
    conn = _connection_returning(
        {
            "status": "success",
            "result": {"succeed": False, "error": "No mesh objects imported from GLB"},
        },
        monkeypatch,
    )

    with pytest.raises(Exception, match="No mesh objects imported from GLB"):
        conn._send_command_locked("import_generated_asset_hunyuan")

    assert conn.sock is not None


def test_send_command_still_raises_cleanly_on_top_level_error_status(monkeypatch) -> None:
    conn = _connection_returning({"status": "error", "message": "Unknown command type: bogus"}, monkeypatch)

    with pytest.raises(Exception, match="Unknown command type: bogus"):
        conn._send_command_locked("bogus")

    # Regression check: this used to get relabeled "Communication error with
    # Blender: ..." and needlessly drop a working socket.
    assert conn.sock is not None


def test_send_command_passes_through_real_success(monkeypatch) -> None:
    conn = _connection_returning(
        {"status": "success", "result": {"succeed": True, "name": "Cube"}}, monkeypatch
    )

    assert conn._send_command_locked("import_generated_asset_hunyuan") == {"succeed": True, "name": "Cube"}
