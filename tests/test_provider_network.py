"""Tests for bounded provider HTTP helpers."""

import importlib.util
import sys
import types

from pathlib import Path

import pytest

NETWORK_PATH = Path(__file__).resolve().parents[1] / "src/blender_mcp/bundled/addon/network.py"
requests = types.ModuleType("requests")
requests.get = None
previous_requests = sys.modules.get("requests")
sys.modules["requests"] = requests
SPEC = importlib.util.spec_from_file_location("blender_mcp_provider_network_test", NETWORK_PATH)
assert SPEC is not None and SPEC.loader is not None
network = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network)
if previous_requests is None:
    del sys.modules["requests"]
else:
    sys.modules["requests"] = previous_requests


class Response:
    """Small requests.Response stand-in for bounded-download tests."""

    def __init__(self, chunks, *, content_type="application/octet-stream", content_length=None) -> None:
        self._chunks = chunks
        self.content = b"".join(chunks)
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size):
        assert chunk_size == network.CHUNK_BYTES
        yield from self._chunks


def test_streamed_download_enforces_actual_byte_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(network.requests, "get", lambda *_args, **_kwargs: Response([b"1234", b"5678"]))
    destination = tmp_path / "asset.bin"

    with pytest.raises(ValueError, match="exceeded"):
        network.download_file("https://example.invalid/asset", str(destination), max_bytes=7)


def test_json_fetch_enforces_declared_size_before_decode(monkeypatch) -> None:
    response = Response([b"{}"], content_length=network.MAX_JSON_BYTES + 1)
    monkeypatch.setattr(network.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(ValueError, match="declares"):
        network.get_json("https://example.invalid/catalog")
