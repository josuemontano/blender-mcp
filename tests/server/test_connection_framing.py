r"""
Regression coverage for BlenderConnection's newline-delimited framing.

Mirrors tests/test_socket_unicode.py on the addon side: `receive_full_response`
now reads up to a `\n` terminator instead of retrying json.loads() on a
growing buffer, and any bytes past that terminator are kept in
`self._recv_buffer` instead of being discarded. `send_command_locked` also
now tags each command with an "id" and checks the response echoes it back,
so a desynced stream fails loudly instead of returning the wrong response.
"""

from __future__ import annotations

import json

import pytest

from blender_mcp.server.connection import BlenderConnection


class ScriptedSocket:
    """Fake socket returning pre-scripted recv() chunks, one per call."""

    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)
        self.sent: list[bytes] = []

    def settimeout(self, timeout) -> None:
        pass

    def recv(self, bufsize):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def test_two_frames_in_one_recv_are_not_glued_together() -> None:
    r"""
    Two newline-terminated responses landing in a single recv() must not be
    concatenated into one - the first call to receive_full_response() should
    return only the first frame, leaving the second buffered for the next
    call instead of failing to parse "response1\nresponse2" as one JSON value.
    """
    first = json.dumps({"status": "success", "result": {"n": 1}}).encode("utf-8") + b"\n"
    second = json.dumps({"status": "success", "result": {"n": 2}}).encode("utf-8") + b"\n"

    conn = BlenderConnection(host="localhost", port=0)
    sock = ScriptedSocket([first + second])

    line1 = conn.receive_full_response(sock)
    assert json.loads(line1) == {"status": "success", "result": {"n": 1}}

    line2 = conn.receive_full_response(sock)
    assert json.loads(line2) == {"status": "success", "result": {"n": 2}}


def test_oversized_response_without_terminator_raises_instead_of_growing_forever() -> None:
    conn = BlenderConnection(host="localhost", port=0)
    conn._MAX_MESSAGE_BYTES = 100  # keep the test fast
    sock = ScriptedSocket([b"x" * 200])

    with pytest.raises(Exception, match="exceeded max size"):
        conn.receive_full_response(sock)


def test_oversized_terminated_response_raises() -> None:
    r"""
    A single complete (`\n`-terminated) response must be size-checked too.

    The unterminated-buffer check above only bounds "how long can we wait
    without ever seeing a terminator" - it does not stop a response that
    *does* get a `\n` (e.g. because the terminating chunk lands in the same
    recv() call that pushes the buffer past the limit) from being returned
    at any size.
    """
    conn = BlenderConnection(host="localhost", port=0)
    conn._MAX_MESSAGE_BYTES = 100  # keep the test fast
    sock = ScriptedSocket([b"x" * 200 + b"\n"])

    with pytest.raises(Exception, match="exceeded max size"):
        conn.receive_full_response(sock)


def test_response_id_mismatch_raises_and_drops_the_socket(monkeypatch) -> None:
    """
    send_command_locked must not hand back a response meant for another
    request - the lock already prevents this in practice, but a mismatched
    id should fail loudly rather than silently succeed with the wrong data.
    """
    conn = BlenderConnection(host="localhost", port=0)
    conn.sock = ScriptedSocket([])
    monkeypatch.setattr(
        conn,
        "receive_full_response",
        lambda sock: json.dumps({"status": "success", "result": {}, "id": "not-the-request-id"}).encode("utf-8"),
    )

    with pytest.raises(Exception, match="does not match request id"):
        conn.send_command_locked("ping")

    assert conn.sock is None, "a desynced response must invalidate the connection"
