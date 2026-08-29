"""Run with Blender 5.1+: blender --background --factory-startup --python this_file.py."""

# This executable Blender harness intentionally uses module-level setup and
# Blender's dynamically typed object return values.
# ruff: file-ignore[float-equality-comparison, magic-value-comparison, missing-return-type-undocumented-public-function, undocumented-public-function]

from __future__ import annotations

import importlib.util
import sys
import tempfile

from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_ROOT = REPO_ROOT / "src" / "blender_mcp" / "bundled" / "addon"
PACKAGE_NAME = "blender_mcp_liquid_phase1_smoke"
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


class Harness(LiquidPhaseOneHandlersMixin, LiquidHandlersMixin):
    """Expose both liquid phases without starting a socket server."""


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
cache_path = tempfile.mkdtemp(prefix="blendermcp-liquid-phase1-")
domain = handler.create_liquid_domain(
    scene_name="Scene",
    cache_directory=cache_path,
    new_object_name="Phase 1 Domain",
    dimensions=(3.0, 3.0, 3.0),
    resolution_max=24,
)
source = cube("Phase 1 Flow", 0.5, (0.0, 0.0, 0.5))
flow = handler.add_liquid_flow(
    object_name=source.name,
    domain_object_name=domain["object"],
    behavior="INFLOW",
    settings={"use_inflow": True, "subframes": 1},
)
mesh = handler.configure_liquid_mesh(
    domain["object"],
    domain["modifier"],
    {"use_mesh": True, "mesh_scale": 2, "mesh_particle_radius": 2.0, "use_speed_vectors": True},
)
particles = handler.configure_liquid_secondary_particles(
    domain["object"],
    domain["modifier"],
    {"use_spray_particles": True, "sndparticle_sampling_wavecrest": 10},
)
diffusion = handler.configure_liquid_diffusion(domain["object"], domain["modifier"], {"preset": "WATER"})
animation = handler.animate_liquid_flow(
    source.name,
    flow["modifier"],
    domain["object"],
    [
        {"frame": 1.0, "use_inflow": True, "interpolation": "CONSTANT"},
        {"frame": 10.0, "use_inflow": False, "interpolation": "CONSTANT"},
    ],
    subframes=2,
)
guide_object = cube("Phase 1 Guide", 0.75, (0.0, 0.0, 0.0))
guide = handler.create_liquid_guide(
    domain["object"],
    domain["modifier"],
    guide_object.name,
    source="EFFECTOR",
)
forces = handler.configure_liquid_force_fields(
    "Scene",
    domain["object"],
    domain["modifier"],
    [
        {
            "object_name": "Phase 1 Wind",
            "field_type": "WIND",
            "create_if_missing": True,
            "location": (0.0, 0.0, 1.0),
            "rotation_euler": (0.0, 0.0, 0.0),
            "strength": 2.0,
            "shape": "POINT",
            "falloff_type": "SPHERE",
            "noise": 0.0,
            "seed": 1,
            "use_min_distance": False,
            "distance_min": 0.0,
            "use_max_distance": False,
            "distance_max": 0.0,
        }
    ],
    "Phase 1 Forces",
    create_collection=True,
    weights={"wind": 1.0},
)
material = handler.create_liquid_material(
    domain["object"],
    domain["modifier"],
    "Phase 1 Water",
    {"preset": "WATER"},
)
cache = handler.manage_liquid_cache(domain["object"], domain["modifier"], action="STATUS")
sample = handler.sample_liquid_simulation(domain["object"], domain["modifier"], [1], timeout_seconds=15.0)
throwaway = cube("Phase 1 Throwaway", 0.25, (0.0, 0.0, 0.0))
throwaway_effector = handler.add_liquid_effector(
    throwaway.name,
    domain["object"],
    modifier_name="Throwaway Effector",
)
removed = handler.remove_fluid_components(
    [{"object_name": throwaway.name, "modifier_name": throwaway_effector["modifier"]}]
)

assert mesh["estimated_output"]["estimated_longest_axis_resolution"] == 48
assert particles["enabled_particle_types"] == ["SPRAY"]
assert diffusion["represented_kinematic_viscosity_m2_s"] == 1e-6
assert len(animation["keyframes"]) == 2
assert guide["settings"]["guide_source"] == "EFFECTOR"
assert forces["force_fields"][0]["field"]["type"] == "WIND"
assert material["created"] is True
assert cache["cache"]["configuration"]["cache_type"] == "REPLAY"
assert sample["timeline_restored"]["frame"] == 1
assert removed["removed"][0]["fluid_type"] == "EFFECTOR"
print("BLENDER_LIQUID_PHASE1_SMOKE_OK")
