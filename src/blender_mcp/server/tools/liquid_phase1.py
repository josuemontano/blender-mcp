# MCP signatures intentionally spell out the supported Mantaflow surface so
# agents receive a constrained schema instead of arbitrary RNA access.
# ruff: file-ignore[docstring-missing-returns, too-many-arguments, too-many-positional-arguments, undocumented-public-class, undocumented-public-method, unused-function-argument]
"""Typed phase-one tools for Blender Mantaflow liquid workflows."""

import asyncio

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..app import mcp
from .liquid import ExistingPolicy, GuideMode, _call, _dump

MeshGenerator = Literal["IMPROVED", "UNION"]
CacheMeshFormat = Literal["UNI", "OPENVDB", "RAW"]
CombinedParticleExport = Literal["OFF", "SPRAY_FOAM", "SPRAY_BUBBLES", "FOAM_BUBBLES", "SPRAY_FOAM_BUBBLES"]
ParticleBoundary = Literal["DELETE", "PUSHOUT"]
ViscosityPreset = Literal["WATER", "OIL", "HONEY", "MOLTEN", "STYLIZED"]
Interpolation = Literal["CONSTANT", "LINEAR", "BEZIER"]
AnimationPolicy = Literal["INSERT_ONLY", "REPLACE_EXISTING"]
GuideSource = Literal["EFFECTOR", "DOMAIN"]
FieldType = Literal["FORCE", "WIND", "VORTEX", "TURBULENCE", "DRAG"]
FieldShape = Literal["POINT", "LINE", "PLANE", "SURFACE", "POINTS"]
FalloffType = Literal["CONE", "SPHERE", "TUBE"]
MaterialPreset = Literal["WATER", "GLASS", "OIL", "TINTED"]
MaterialAssignment = Literal["APPEND", "REPLACE_SLOT"]
ParticleRepresentation = Literal["OBJECT"]
CacheAction = Literal[
    "STATUS",
    "CONFIGURE",
    "BAKE_DATA",
    "BAKE_GUIDES",
    "BAKE_MESH",
    "BAKE_PARTICLES",
    "BAKE_ALL",
    "PAUSE",
    "FREE_DATA",
    "FREE_GUIDES",
    "FREE_MESH",
    "FREE_PARTICLES",
    "FREE_ALL",
]
CacheType = Literal["REPLAY", "MODULAR", "FINAL", "ALL"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiquidMeshPatch(_StrictModel):
    use_mesh: bool | None = None
    mesh_scale: int | None = Field(default=None, ge=1, le=8)
    mesh_particle_radius: float | None = Field(default=None, gt=0.0, le=10.0)
    mesh_smoothen_pos: int | None = Field(default=None, ge=0, le=100)
    mesh_smoothen_neg: int | None = Field(default=None, ge=0, le=100)
    mesh_concave_upper: float | None = Field(default=None, ge=0.0, le=10.0)
    mesh_concave_lower: float | None = Field(default=None, ge=0.0, le=10.0)
    mesh_generator: MeshGenerator | None = None
    use_speed_vectors: bool | None = None
    cache_mesh_format: CacheMeshFormat | None = None


class LiquidSecondaryParticlePatch(_StrictModel):
    use_spray_particles: bool | None = None
    use_foam_particles: bool | None = None
    use_bubble_particles: bool | None = None
    use_tracer_particles: bool | None = None
    sndparticle_combined_export: CombinedParticleExport | None = None
    sndparticle_boundary: ParticleBoundary | None = None
    sndparticle_life_min: float | None = Field(default=None, ge=0.0, le=10_000.0)
    sndparticle_life_max: float | None = Field(default=None, ge=0.0, le=10_000.0)
    sndparticle_potential_min_wavecrest: float | None = Field(default=None, ge=0.0, le=1_000.0)
    sndparticle_potential_max_wavecrest: float | None = Field(default=None, ge=0.0, le=1_000.0)
    sndparticle_potential_min_trappedair: float | None = Field(default=None, ge=0.0, le=1_000.0)
    sndparticle_potential_max_trappedair: float | None = Field(default=None, ge=0.0, le=1_000.0)
    sndparticle_potential_min_energy: float | None = Field(default=None, ge=0.0, le=1_000.0)
    sndparticle_potential_max_energy: float | None = Field(default=None, ge=0.0, le=1_000.0)
    sndparticle_sampling_wavecrest: int | None = Field(default=None, ge=0, le=10_000)
    sndparticle_sampling_trappedair: int | None = Field(default=None, ge=0, le=10_000)
    sndparticle_update_radius: int | None = Field(default=None, ge=1, le=4)
    sndparticle_bubble_buoyancy: float | None = Field(default=None, ge=0.0, le=100.0)
    sndparticle_bubble_drag: float | None = Field(default=None, ge=0.0, le=100.0)
    particle_scale: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_ranges(self) -> "LiquidSecondaryParticlePatch":
        pairs = (
            (self.sndparticle_life_min, self.sndparticle_life_max, "life"),
            (
                self.sndparticle_potential_min_wavecrest,
                self.sndparticle_potential_max_wavecrest,
                "wavecrest potential",
            ),
            (
                self.sndparticle_potential_min_trappedair,
                self.sndparticle_potential_max_trappedair,
                "trapped-air potential",
            ),
            (self.sndparticle_potential_min_energy, self.sndparticle_potential_max_energy, "energy potential"),
        )
        for minimum, maximum, label in pairs:
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{label} minimum must be <= maximum")
        return self


class LiquidDiffusionConfig(_StrictModel):
    preset: ViscosityPreset | None = None
    use_diffusion: bool | None = None
    viscosity_base: float | None = Field(default=None, ge=0.0, le=10.0)
    viscosity_exponent: int | None = Field(default=None, ge=0, le=10)
    use_viscosity: bool | None = None
    viscosity_value: float | None = Field(default=None, ge=0.0, le=10.0)
    surface_tension: float | None = Field(default=None, ge=0.0, le=100.0)
    dynamic_viscosity_pa_s: float | None = Field(default=None, gt=0.0)
    density_kg_m3: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def validate_source(self) -> "LiquidDiffusionConfig":
        if (self.dynamic_viscosity_pa_s is None) != (self.density_kg_m3 is None):
            raise ValueError("dynamic_viscosity_pa_s and density_kg_m3 must be supplied together")
        direct = self.viscosity_base is not None or self.viscosity_exponent is not None
        sources = int(self.preset is not None) + int(self.dynamic_viscosity_pa_s is not None) + int(direct)
        if sources > 1:
            raise ValueError("Choose one viscosity source: preset, dynamic/density conversion, or base/exponent")
        return self


class LiquidFlowKeyframe(_StrictModel):
    frame: float = Field(ge=-1_000_000.0, le=1_000_000.0)
    use_inflow: bool | None = None
    use_initial_velocity: bool | None = None
    velocity_factor: float | None = None
    velocity_normal: float | None = None
    velocity_random: float | None = Field(default=None, ge=0.0)
    interpolation: Interpolation = "CONSTANT"

    @model_validator(mode="after")
    def require_value(self) -> "LiquidFlowKeyframe":
        values = self.model_dump(exclude={"frame", "interpolation"}, exclude_none=True)
        if len(values) != 1:
            raise ValueError("Each record must key exactly one flow property")
        return self


class EffectorWeightsPatch(_StrictModel):
    all: float | None = Field(default=None, ge=-200.0, le=200.0)
    gravity: float | None = Field(default=None, ge=-200.0, le=200.0)
    force: float | None = Field(default=None, ge=-200.0, le=200.0)
    vortex: float | None = Field(default=None, ge=-200.0, le=200.0)
    magnetic: float | None = Field(default=None, ge=-200.0, le=200.0)
    wind: float | None = Field(default=None, ge=-200.0, le=200.0)
    curve_guide: float | None = Field(default=None, ge=-200.0, le=200.0)
    texture: float | None = Field(default=None, ge=-200.0, le=200.0)
    harmonic: float | None = Field(default=None, ge=-200.0, le=200.0)
    charge: float | None = Field(default=None, ge=-200.0, le=200.0)
    lennardjones: float | None = Field(default=None, ge=-200.0, le=200.0)
    boid: float | None = Field(default=None, ge=-200.0, le=200.0)
    turbulence: float | None = Field(default=None, ge=-200.0, le=200.0)
    drag: float | None = Field(default=None, ge=-200.0, le=200.0)
    smokeflow: float | None = Field(default=None, ge=-200.0, le=200.0)


class LiquidForceFieldSpec(_StrictModel):
    object_name: str
    field_type: FieldType
    create_if_missing: bool = False
    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_euler: tuple[float, float, float] = (0.0, 0.0, 0.0)
    strength: float = 0.0
    shape: FieldShape = "POINT"
    falloff_type: FalloffType = "SPHERE"
    noise: float = Field(default=0.0, ge=0.0, le=10.0)
    seed: int = Field(default=1, ge=1, le=128)
    use_min_distance: bool = False
    distance_min: float = Field(default=0.0, ge=0.0)
    use_max_distance: bool = False
    distance_max: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_distances(self) -> "LiquidForceFieldSpec":
        if self.use_max_distance and self.use_min_distance and self.distance_max < self.distance_min:
            raise ValueError("distance_max must be >= distance_min")
        return self


class LiquidMaterialConfig(_StrictModel):
    preset: MaterialPreset = "WATER"
    base_color: tuple[float, float, float, float] | None = None
    transmission_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    ior: float | None = Field(default=None, ge=1.0, le=3.0)
    roughness: float | None = Field(default=None, ge=0.0, le=1.0)
    volume_absorption_color: tuple[float, float, float, float] | None = None
    volume_density: float | None = Field(default=None, ge=0.0)


class LiquidCachePatch(_StrictModel):
    cache_directory: str | None = None
    cache_type: CacheType | None = None
    cache_data_format: Literal["UNI", "OPENVDB", "RAW"] | None = None
    cache_mesh_format: CacheMeshFormat | None = None
    cache_particle_format: Literal["UNI", "OPENVDB", "RAW"] | None = None
    cache_frame_start: int | None = None
    cache_frame_end: int | None = None
    cache_frame_offset: int | None = None
    cache_resumable: bool | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "LiquidCachePatch":
        if (
            self.cache_frame_start is not None
            and self.cache_frame_end is not None
            and self.cache_frame_start > self.cache_frame_end
        ):
            raise ValueError("cache_frame_start must be <= cache_frame_end")
        return self


class FluidComponentTarget(_StrictModel):
    object_name: str
    modifier_name: str
    remove_owned_helper_object: bool = False


@mcp.tool()
async def configure_liquid_mesh(
    ctx: Context, domain_object_name: str, modifier_name: str, patch: LiquidMeshPatch
) -> dict:
    """Patch render-surface generation on one unbaked liquid domain."""
    return await asyncio.to_thread(
        _call,
        "configure_liquid_mesh",
        {"domain_object_name": domain_object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [domain_object_name],
    )


@mcp.tool()
async def configure_liquid_secondary_particles(
    ctx: Context, domain_object_name: str, modifier_name: str, patch: LiquidSecondaryParticlePatch
) -> dict:
    """Patch spray, foam, bubble, and tracer generation on an unbaked liquid domain."""
    return await asyncio.to_thread(
        _call,
        "configure_liquid_secondary_particles",
        {"domain_object_name": domain_object_name, "modifier_name": modifier_name, "patch": _dump(patch)},
        [domain_object_name],
    )


@mcp.tool()
async def configure_liquid_diffusion(
    ctx: Context, domain_object_name: str, modifier_name: str, config: LiquidDiffusionConfig
) -> dict:
    """Configure viscosity and surface tension from direct values, a versioned preset, or SI inputs."""
    return await asyncio.to_thread(
        _call,
        "configure_liquid_diffusion",
        {"domain_object_name": domain_object_name, "modifier_name": modifier_name, "config": _dump(config)},
        [domain_object_name],
    )


@mcp.tool()
async def animate_liquid_flow(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    domain_object_name: str,
    keyframes: list[LiquidFlowKeyframe],
    policy: AnimationPolicy = "INSERT_ONLY",
    subframes: Annotated[int, Field(ge=0, le=200)] | None = None,
) -> dict:
    """Key liquid flow settings with explicit merge policy and per-key interpolation."""
    return await asyncio.to_thread(
        _call,
        "animate_liquid_flow",
        {
            "object_name": object_name,
            "modifier_name": modifier_name,
            "domain_object_name": domain_object_name,
            "keyframes": [item.model_dump(exclude_none=True) for item in keyframes],
            "policy": policy,
            "subframes": subframes,
        },
        [object_name, domain_object_name],
    )


@mcp.tool()
async def create_liquid_guide(
    ctx: Context,
    domain_object_name: str,
    domain_modifier_name: str,
    guide_object_name: str,
    source: GuideSource = "EFFECTOR",
    guide_modifier_name: str = "Liquid Guide",
    existing_policy: ExistingPolicy = "ERROR",
    guide_mode: GuideMode = "OVERRIDE",
    velocity_factor: float = 1.0,
    guide_parent_domain_object_name: str | None = None,
    guide_collection_name: str | None = None,
    cache_frame_start: int | None = None,
    cache_frame_end: int | None = None,
    guide_alpha: Annotated[float, Field(ge=1.0, le=100.0)] | None = None,
    guide_beta: Annotated[int, Field(ge=1, le=50)] | None = None,
    guide_vel_factor: Annotated[float, Field(ge=0.0, le=100.0)] | None = None,
) -> dict:
    """Create an effector guide or connect one liquid domain as another domain's guide source."""
    return await asyncio.to_thread(
        _call,
        "create_liquid_guide",
        {
            "domain_object_name": domain_object_name,
            "domain_modifier_name": domain_modifier_name,
            "guide_object_name": guide_object_name,
            "source": source,
            "guide_modifier_name": guide_modifier_name,
            "existing_policy": existing_policy,
            "guide_mode": guide_mode,
            "velocity_factor": velocity_factor,
            "guide_parent_domain_object_name": guide_parent_domain_object_name,
            "guide_collection_name": guide_collection_name,
            "cache_frame_start": cache_frame_start,
            "cache_frame_end": cache_frame_end,
            "guide_alpha": guide_alpha,
            "guide_beta": guide_beta,
            "guide_vel_factor": guide_vel_factor,
        },
        [name for name in [domain_object_name, guide_object_name, guide_parent_domain_object_name] if name],
    )


@mcp.tool()
async def configure_liquid_force_fields(
    ctx: Context,
    scene_name: str,
    domain_object_name: str,
    modifier_name: str,
    fields: list[LiquidForceFieldSpec],
    force_collection_name: str,
    create_collection: bool = False,
    weights: EffectorWeightsPatch | None = None,
) -> dict:
    """Create or configure bounded force fields and scope their influence to one liquid domain."""
    return await asyncio.to_thread(
        _call,
        "configure_liquid_force_fields",
        {
            "scene_name": scene_name,
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "fields": [item.model_dump() for item in fields],
            "force_collection_name": force_collection_name,
            "create_collection": create_collection,
            "weights": _dump(weights),
        },
        [domain_object_name, *[item.object_name for item in fields]],
    )


@mcp.tool()
async def create_liquid_material(
    ctx: Context,
    domain_object_name: str,
    modifier_name: str,
    material_name: str,
    config: LiquidMaterialConfig | None = None,
    existing_policy: ExistingPolicy = "ERROR",
    assignment: MaterialAssignment = "APPEND",
    slot_index: Annotated[int, Field(ge=0)] | None = None,
) -> dict:
    """Create and assign a Principled transparent liquid material without clearing unrelated slots."""
    return await asyncio.to_thread(
        _call,
        "create_liquid_material",
        {
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "material_name": material_name,
            "config": (config or LiquidMaterialConfig()).model_dump(exclude_none=True),
            "existing_policy": existing_policy,
            "assignment": assignment,
            "slot_index": slot_index,
        },
        [domain_object_name],
    )


@mcp.tool()
async def create_secondary_particle_render_setup(
    ctx: Context,
    domain_object_name: str,
    modifier_name: str,
    representation: ParticleRepresentation = "OBJECT",
    instance_object_name: str | None = None,
    create_instance_sphere: bool = False,
    helper_collection_name: str = "Liquid Particle Helpers",
    helper_object_name: str = "Liquid Particle Instance",
    material_name: str | None = None,
    display_percentage: Annotated[int, Field(ge=1, le=100)] = 25,
    particle_size: Annotated[float, Field(gt=0.0)] | None = None,
    max_systems: Annotated[int, Field(ge=1, le=64)] = 16,
) -> dict:
    """Configure discovered baked Mantaflow particle systems for bounded object instancing."""
    return await asyncio.to_thread(
        _call,
        "create_secondary_particle_render_setup",
        {
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "representation": representation,
            "instance_object_name": instance_object_name,
            "create_instance_sphere": create_instance_sphere,
            "helper_collection_name": helper_collection_name,
            "helper_object_name": helper_object_name,
            "material_name": material_name,
            "display_percentage": display_percentage,
            "particle_size": particle_size,
            "max_systems": max_systems,
        },
        [name for name in [domain_object_name, instance_object_name] if name],
    )


@mcp.tool()
async def sample_liquid_simulation(
    ctx: Context,
    domain_object_name: str,
    modifier_name: str,
    frames: list[int],
    timeout_seconds: Annotated[float, Field(gt=0.0, le=300.0)] = 30.0,
    boundary_tolerance_cells: Annotated[float, Field(ge=0.0, le=10.0)] = 1.0,
) -> dict:
    """Evaluate up to 32 cached/replay frames and return numerical mesh, particle, and bounds evidence."""
    return await asyncio.to_thread(
        _call,
        "sample_liquid_simulation",
        {
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "frames": frames,
            "timeout_seconds": timeout_seconds,
            "boundary_tolerance_cells": boundary_tolerance_cells,
        },
        [domain_object_name],
    )


@mcp.tool()
async def manage_liquid_cache(
    ctx: Context,
    domain_object_name: str,
    modifier_name: str,
    action: CacheAction = "STATUS",
    patch: LiquidCachePatch | None = None,
    confirm_bake: bool = False,
    confirm_free: bool = False,
    confirm_external_path: bool = False,
    confirm_external_overwrite: bool = False,
    max_bake_frames: Annotated[int, Field(ge=1, le=10_000)] = 250,
    max_existing_cache_bytes: Annotated[int, Field(ge=0)] = 10_000_000_000,
) -> dict:
    """Inspect, configure, bake, pause, or explicitly free exact Mantaflow cache stages."""
    return await asyncio.to_thread(
        _call,
        "manage_liquid_cache",
        {
            "domain_object_name": domain_object_name,
            "modifier_name": modifier_name,
            "action": action,
            "patch": _dump(patch),
            "confirm_bake": confirm_bake,
            "confirm_free": confirm_free,
            "confirm_external_path": confirm_external_path,
            "confirm_external_overwrite": confirm_external_overwrite,
            "max_bake_frames": max_bake_frames,
            "max_existing_cache_bytes": max_existing_cache_bytes,
        },
        [domain_object_name],
    )


@mcp.tool()
async def remove_fluid_components(
    ctx: Context,
    targets: list[FluidComponentTarget],
    accept_orphaned_cache: bool = False,
) -> dict:
    """Remove exact fluid modifiers and optionally MCP-owned helper objects after complete preflight."""
    return await asyncio.to_thread(
        _call,
        "remove_fluid_components",
        {
            "targets": [item.model_dump() for item in targets],
            "accept_orphaned_cache": accept_orphaned_cache,
        },
        [item.object_name for item in targets],
    )
