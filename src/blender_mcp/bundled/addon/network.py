"""Bounded HTTP helpers used by optional asset providers."""

import json

from typing import Any

import requests

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 60
DEFAULT_TIMEOUT = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
MAX_JSON_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 256 * 1024


def _declared_size(response) -> int | None:
    value = response.headers.get("Content-Length")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _check_declared_size(response, max_bytes: int) -> None:
    size = _declared_size(response)
    if size is not None and size > max_bytes:
        raise ValueError(f"Download declares {size} bytes, exceeding the {max_bytes}-byte limit")


def get_json(url: str, *, headers: dict | None = None, params: dict | None = None) -> Any:
    """Fetch and decode one bounded JSON document."""
    response = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    _check_declared_size(response, MAX_JSON_BYTES)
    content = response.content
    if len(content) > MAX_JSON_BYTES:
        raise ValueError(f"JSON response exceeded the {MAX_JSON_BYTES}-byte limit")
    return json.loads(content)


def download_file(
    url: str,
    filepath: str,
    *,
    headers: dict | None = None,
    max_bytes: int,
) -> int:
    """Stream one response to an explicit path while enforcing a byte limit."""
    response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT, stream=True)
    response.raise_for_status()
    _check_declared_size(response, max_bytes)
    written = 0
    with open(filepath, "wb") as file_handle:
        for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                raise ValueError(f"Download exceeded the {max_bytes}-byte limit")
            file_handle.write(chunk)
    return written


def get_bytes(url: str, *, headers: dict | None = None, max_bytes: int) -> tuple[bytes, str]:
    """Fetch one bounded binary response and return bytes plus Content-Type."""
    response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT, stream=True)
    response.raise_for_status()
    _check_declared_size(response, max_bytes)
    chunks = []
    received = 0
    for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            raise ValueError(f"Download exceeded the {max_bytes}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks), response.headers.get("Content-Type", "")
