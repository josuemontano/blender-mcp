"""Shared structured-result envelope for MCP tool return values."""

from typing import Any


def ok(
    data: Any = None,
    *,
    warnings: list[str] | None = None,
    changed_objects: list[str] | None = None,
) -> dict:
    return {
        "ok": True,
        "data": data,
        "error": None,
        "warnings": warnings or [],
        "changed_objects": changed_objects or [],
    }
