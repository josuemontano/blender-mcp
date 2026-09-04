"""
Tests that a Blender-side failure never comes back as a successful result.

Many addon handlers catch their own exceptions and return an ad-hoc failure
shape ({"error": ...}, {"succeed": False, "error": ...}, or a bare
"Error: ..." string) instead of raising. The addon's own dispatcher then
wraps that as {"status": "success", "result": <value>}, since only a raised
exception produces {"status": "error", ...}. `ad_hoc_failure_message` and
`send_command_locked` are the safety net that catches this on the MCP
server side regardless of which addon version is installed.
"""

from __future__ import annotations

import json

import pytest

from blender_mcp.server.connection import BlenderConnection, ad_hoc_failure_message


class FakeSocket:
    """Minimal stand-in so send_command_locked never touches a real socket."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def settimeout(self, value: float) -> None:
        pass


def _connection_returning(payload: dict, monkeypatch) -> BlenderConnection:
    conn = BlenderConnection(host="localhost", port=0)
    sock = FakeSocket()
    conn.sock = sock

    def fake_receive_full_response(_sock):
        # Echo back whatever id send_command_locked generated for the
        # command it just sent, same as a real addon response would.
        sent_command = json.loads(sock.sent[-1].decode("utf-8"))
        return json.dumps({**payload, "id": sent_command.get("id")}).encode("utf-8")

    monkeypatch.setattr(conn, "receive_full_response", fake_receive_full_response)
    return conn


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"error": "API key is not given"}, "API key is not given"),
        ({"succeed": False, "error": "No mesh objects imported from GLB"}, "No mesh objects imported from GLB"),
        ({"succeed": False}, "{'succeed': False}"),
        ("Error: Unknown import mode!", "Error: Unknown import mode!"),
    ],
)
def test_ad_hoc_failure_message_detects_known_failure_shapes(result, expected) -> None:
    assert ad_hoc_failure_message(result) == expected


@pytest.mark.parametrize(
    "result",
    [
        {"succeed": True, "name": "Cube", "type": "MESH"},
        {"enabled": True, "message": "Sketchfab integration is enabled and ready to use."},
        {"name": "Scene", "object_count": 3, "objects": []},
        {"pong": True},
        None,
        "job_12345",
        [1, 2, 3],
    ],
)
def test_ad_hoc_failure_message_leaves_real_success_alone(result) -> None:
    assert ad_hoc_failure_message(result) is None


def test_send_command_raises_on_nested_error_dict(monkeypatch) -> None:
    conn = _connection_returning(
        {"status": "success", "result": {"error": "API key is not given"}}, monkeypatch
    )

    with pytest.raises(Exception, match="API key is not given"):
        conn.send_command_locked("download_sketchfab_model")

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
        conn.send_command_locked("import_polyhaven_asset")

    assert conn.sock is not None


def test_send_command_still_raises_cleanly_on_top_level_error_status(monkeypatch) -> None:
    conn = _connection_returning({"status": "error", "message": "Unknown command type: bogus"}, monkeypatch)

    with pytest.raises(Exception, match="Unknown command type: bogus"):
        conn.send_command_locked("bogus")

    # Regression check: this used to get relabeled "Communication error with
    # Blender: ..." and needlessly drop a working socket.
    assert conn.sock is not None


def test_send_command_passes_through_real_success(monkeypatch) -> None:
    conn = _connection_returning(
        {"status": "success", "result": {"succeed": True, "name": "Cube"}}, monkeypatch
    )

    assert conn.send_command_locked("import_polyhaven_asset") == {"succeed": True, "name": "Cube"}
