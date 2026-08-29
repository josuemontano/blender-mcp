"""Run with Blender 5.1+: blender --background --factory-startup --python this_file.py."""

# This executable Blender harness intentionally uses module-level setup and
# Blender's dynamically typed object return values.
# ruff: file-ignore[magic-value-comparison, missing-return-type-undocumented-public-function, undocumented-public-function]

from __future__ import annotations

import importlib.util
import sys
import tempfile

from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_ROOT = REPO_ROOT / "src" / "blender_mcp" / "bundled" / "addon"
PACKAGE_NAME = "blender_mcp_liquid_phase2_smoke"
spec = importlib.util.spec_from_file_location(
    PACKAGE_NAME,
    ADDON_ROOT / "__init__.py",
    submodule_search_locations=[str(ADDON_ROOT)],
)
assert spec is not None and spec.loader is not None
addon = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE_NAME] = addon
spec.loader.exec_module(addon)
LiquidHandlersMixin = sys.modules[f"{PACKAGE_NAME}.handlers.liquid"].LiquidHandlersMixin
LiquidPhaseOneHandlersMixin = sys.modules[f"{PACKAGE_NAME}.handlers.liquid_phase1"].LiquidPhaseOneHandlersMixin
LiquidPhaseTwoHandlersMixin = sys.modules[f"{PACKAGE_NAME}.handlers.liquid_phase2"].LiquidPhaseTwoHandlersMixin


class Harness(LiquidPhaseTwoHandlersMixin, LiquidPhaseOneHandlersMixin, LiquidHandlersMixin):
    """Expose all liquid phases without starting a socket server."""


def cube(name: str, size: float, location: tuple[float, float, float]):
    half = size * 0.5
    vertices = [
        (-half, -half, -half),
        (half, -half, -half),
        (half, half, -half),
        (-half, half, -half),
        (-half, -half, half),
        (half, -half, half),
        (half, half, half),
        (-half, half, half),
    ]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7)]
    mesh = bpy.data.meshes.new(f"{name} Mesh")
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


handler = Harness()
domain_cache = tempfile.mkdtemp(prefix="blendermcp-liquid-phase2-source-")
variant_cache = tempfile.mkdtemp(prefix="blendermcp-liquid-phase2-variant-")
domain = handler.create_liquid_domain(
    scene_name="Scene",
    cache_directory=domain_cache,
    new_object_name="Phase 2 Domain",
    dimensions=(4.0, 4.0, 4.0),
    resolution_max=24,
)
source = cube("Phase 2 Source", 1.0, (0.5, -0.5, 0.25))
proxy = handler.create_liquid_proxy_rig(
    "Scene",
    source.name,
    "Phase 2 Proxy",
    domain["object"],
    domain["modifier"],
    "EFFECTOR",
    geometry="BOX",
    validation_frames=[1, 2],
)
capsule = handler.create_liquid_proxy_rig(
    "Scene",
    source.name,
    "Phase 2 Capsule Proxy",
    domain["object"],
    domain["modifier"],
    "EFFECTOR",
    geometry="CAPSULE",
    driver="PARENT",
)
convex = handler.create_liquid_proxy_rig(
    "Scene",
    source.name,
    "Phase 2 Hull Proxy",
    domain["object"],
    domain["modifier"],
    "EFFECTOR",
    geometry="CONVEX_HULL",
)
decimated = handler.create_liquid_proxy_rig(
    "Scene",
    source.name,
    "Phase 2 Decimated Proxy",
    domain["object"],
    domain["modifier"],
    "FLOW",
    geometry="DECIMATED",
)
supplied_object = cube("Phase 2 Supplied Proxy", 0.75, (0.0, 0.0, 0.0))
supplied = handler.create_liquid_proxy_rig(
    "Scene",
    source.name,
    supplied_object.name,
    domain["object"],
    domain["modifier"],
    "EFFECTOR",
    geometry="SUPPLIED",
)
variant = handler.duplicate_liquid_setup_variant(
    domain["object"],
    domain["modifier"],
    "Phase 2 Domain Preview",
    "Phase 2 Variant",
    "Preview",
    variant_cache,
)
performance = handler.analyze_liquid_performance(domain["object"], domain["modifier"])

assert proxy["proxy"] == "Phase 2 Proxy"
assert max(record["maximum_matrix_error"] for record in proxy["transform_validation"]) < 1e-5
assert capsule["driver"] == "PARENT"
assert convex["geometry"] == "CONVEX_HULL"
assert decimated["role"] == "FLOW"
assert supplied["created_proxy"] is False
assert variant["cache_directory_resolved"] != domain["cache_directory_resolved"]
assert variant["disabled_domain"] == "Phase 2 Domain Preview"
assert performance["claims"]["exact_peak_memory"] is None
assert performance["measured_evaluation"]["performed"] is False
print("BLENDER_LIQUID_PHASE2_SMOKE_OK")
