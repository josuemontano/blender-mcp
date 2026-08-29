"""Shared paths and loader for the test suite.

These tests load the bundled addon package as source (it cannot be imported
without bpy), so they need the repo root rather than the tests directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_ADDON = REPO_ROOT / "src" / "blender_mcp" / "bundled" / "addon" / "__init__.py"


def load_addon_package(monkeypatch, name):
    """Load the bundled addon package under a scratch dotted module name.

    Internal relative imports (`from . import helpers`, `from .handlers import
    mesh`, ...) need `submodule_search_locations` and the module registered in
    `sys.modules` under `name` *before* `exec_module` runs, so those relative
    imports resolve against this package rather than failing or leaking into
    an unrelated module.

    Those relative imports also cache each submodule in `sys.modules` under
    `name.<submodule>` as a side effect - entries `monkeypatch` never touches,
    so they'd survive into the next test that reuses `name` and get reused
    stale (with a previous test's mocked bpy baked into their closures)
    instead of re-executing against the current mocks. Purge them up front.
    """
    prefix = f"{name}."
    for key in [k for k in sys.modules if k == name or k.startswith(prefix)]:
        monkeypatch.delitem(sys.modules, key, raising=False)

    spec = importlib.util.spec_from_file_location(
        name, ROOT_ADDON, submodule_search_locations=[str(ROOT_ADDON.parent)]
    )
    addon = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, addon)
    spec.loader.exec_module(addon)
    return addon
