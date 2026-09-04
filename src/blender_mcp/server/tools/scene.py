# ruff: file-ignore[docstring-missing-exception, docstring-missing-returns, too-many-arguments, too-many-positional-arguments, unused-function-argument]
"""Typed scene composition, native geometry, hierarchy, and modifier tools."""

import asyncio

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..app import mcp
from ..connection import get_blender_connection
from .envelope import STALE_INDEX_WARNING, ok


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MeshGeometry(_StrictModel):
    """Declarative mesh topology in object-local coordinates."""

    kind: Literal["MESH"] = "MESH"
    vertices: Annotated[list[tuple[float, float, float]], Field(max_length=1_000_000)]
    edges: Annotated[list[tuple[int, int]], Field(max_length=2_000_000)] = Field(default_factory=list)
    faces: Annotated[list[list[int]], Field(max_length=1_000_000)] = Field(default_factory=list)


class SplineRecord(_StrictModel):
    """One curve or surface spline and its interpolation settings."""

    type: Literal["POLY", "BEZIER", "NURBS"] = "POLY"
    points: Annotated[list[tuple[float, float, float]], Field(min_length=1, max_length=100_000)]
    cyclic: bool = False
    order_u: Annotated[int, Field(ge=2, le=64)] = 4
    endpoint: bool = True


class SplineGeometry(_StrictModel):
    """Curve or surface data composed from one or more splines."""

    kind: Literal["CURVE", "SURFACE"]
    dimensions: Literal["2D", "3D"] = "3D"
    splines: Annotated[list[SplineRecord], Field(min_length=1, max_length=10_000)]
    resolution_u: Annotated[int, Field(ge=1, le=1024)] = 12
    bevel_depth: Annotated[float, Field(ge=0)] = 0.0
    bevel_resolution: Annotated[int, Field(ge=0, le=32)] = 4
    extrude: Annotated[float, Field(ge=0)] = 0.0


class TextGeometry(_StrictModel):
    """Editable Blender font geometry."""

    kind: Literal["TEXT"] = "TEXT"
    body: Annotated[str, Field(max_length=100_000)]
    size: Annotated[float, Field(gt=0)] = 1.0
    extrude: Annotated[float, Field(ge=0)] = 0.0
    bevel_depth: Annotated[float, Field(ge=0)] = 0.0
    align_x: Literal["LEFT", "CENTER", "RIGHT", "JUSTIFY", "FLUSH"] = "LEFT"
    align_y: Literal["TOP_BASELINE", "TOP", "CENTER", "BOTTOM", "BOTTOM_BASELINE"] = "TOP_BASELINE"


class MetaElement(_StrictModel):
    """One metaball family element."""

    co: tuple[float, float, float]
    radius: Annotated[float, Field(gt=0)] = 1.0
    stiffness: Annotated[float, Field(ge=0, le=10)] = 2.0
    type: Literal["BALL", "CAPSULE", "PLANE", "ELLIPSOID", "CUBE"] = "BALL"


class MetaGeometry(_StrictModel):
    """Editable metaball data containing explicit elements."""

    kind: Literal["META"] = "META"
    elements: Annotated[list[MetaElement], Field(min_length=1, max_length=10_000)]
    resolution: Annotated[float, Field(gt=0)] = 0.4
    render_resolution: Annotated[float, Field(gt=0)] = 0.2
    threshold: Annotated[float, Field(gt=0)] = 0.6


class LatticeGeometry(_StrictModel):
    """Lattice resolution specification."""

    kind: Literal["LATTICE"] = "LATTICE"
    points_u: Annotated[int, Field(ge=2, le=64)] = 2
    points_v: Annotated[int, Field(ge=2, le=64)] = 2
    points_w: Annotated[int, Field(ge=2, le=64)] = 2


class PointCloudGeometry(_StrictModel):
    """Native point-cloud positions and optional point radii."""

    kind: Literal["POINTCLOUD"] = "POINTCLOUD"
    points: Annotated[list[tuple[float, float, float]], Field(max_length=2_000_000)]
    radii: list[float] | None = None

    @model_validator(mode="after")
    def validate_radii(self) -> "PointCloudGeometry":
        """Require one finite, non-negative radius per point when supplied."""
        if self.radii is not None:
            if len(self.radii) != len(self.points):
                raise ValueError("radii must contain one value per point")
            if any(radius < 0 for radius in self.radii):
                raise ValueError("radii must be non-negative")
        return self


class VolumeGeometry(_StrictModel):
    """OpenVDB-backed volume data."""

    kind: Literal["VOLUME"] = "VOLUME"
    filepath: Annotated[str, Field(min_length=1)]
    is_sequence: bool = False
    frame_start: int = 1
    frame_duration: Annotated[int, Field(ge=1)] = 1


GeometrySpec = Annotated[
    MeshGeometry | SplineGeometry | TextGeometry | MetaGeometry | LatticeGeometry | PointCloudGeometry | VolumeGeometry,
    Field(discriminator="kind"),
]


class TransformPatch(_StrictModel):
    """Partial transform channels or a complete 4x4 matrix."""

    location: tuple[float, float, float] | None = None
    rotation_euler: tuple[float, float, float] | None = None
    rotation_quaternion: tuple[float, float, float, float] | None = None
    rotation_axis_angle: tuple[float, float, float, float] | None = None
    scale: tuple[float, float, float] | None = None
    matrix: tuple[tuple[float, float, float, float], ...] | None = None

    @model_validator(mode="after")
    def validate_representation(self) -> "TransformPatch":
        """Reject ambiguous rotation and matrix representations."""
        rotations = (self.rotation_euler, self.rotation_quaternion, self.rotation_axis_angle)
        if not any(value is not None for value in (self.location, *rotations, self.scale, self.matrix)):
            raise ValueError("patch must set at least one transform field")
        if sum(value is not None for value in rotations) > 1:
            raise ValueError("Supply at most one rotation representation")
        if self.matrix is not None:
            if len(self.matrix) != 4 or any(len(row) != 4 for row in self.matrix):
                raise ValueError("matrix must be 4x4")
            if any(value is not None for value in (self.location, *rotations, self.scale)):
                raise ValueError("matrix is mutually exclusive with component transforms")
        if self.scale is not None and any(value == 0 for value in self.scale):
            raise ValueError("scale components must be non-zero")
        return self


class InstanceTransform(_StrictModel):
    """Local transform assigned to one generated duplicate or instance."""

    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    @model_validator(mode="after")
    def validate_scale(self) -> "InstanceTransform":
        """Reject degenerate instance transforms."""
        if any(value == 0 for value in self.scale):
            raise ValueError("scale components must be non-zero")
        return self


class HierarchyAssignment(_StrictModel):
    """One explicit object-parent or bone-parent assignment."""

    child_object_name: str = Field(min_length=1)
    parent_object_name: str | None = None
    parent_bone_name: str | None = None


class ConstraintSpec(_StrictModel):
    """Allowlisted object constraint and settings patch."""

    name: str = Field(min_length=1)
    type: Literal[
        "COPY_LOCATION",
        "COPY_ROTATION",
        "COPY_SCALE",
        "COPY_TRANSFORMS",
        "CHILD_OF",
        "DAMPED_TRACK",
        "TRACK_TO",
        "LOCKED_TRACK",
        "FOLLOW_PATH",
        "CLAMP_TO",
        "LIMIT_LOCATION",
        "LIMIT_ROTATION",
        "LIMIT_SCALE",
        "LIMIT_DISTANCE",
        "STRETCH_TO",
        "SHRINKWRAP",
    ]
    target_object_name: str | None = None
    subtarget: str | None = None
    influence: float = Field(default=1.0, ge=0, le=1)
    settings: dict[str, Any] = Field(default_factory=dict)


class ModifierSpec(_StrictModel):
    """Allowlisted non-Geometry-Nodes modifier and settings patch."""

    name: str = Field(min_length=1)
    type: Literal[
        "ARRAY",
        "BEVEL",
        "BOOLEAN",
        "BUILD",
        "CAST",
        "CURVE",
        "DECIMATE",
        "DISPLACE",
        "LATTICE",
        "MASK",
        "MESH_DEFORM",
        "MIRROR",
        "REMESH",
        "SCREW",
        "SHRINKWRAP",
        "SIMPLE_DEFORM",
        "SKIN",
        "SMOOTH",
        "SOLIDIFY",
        "SUBSURF",
        "TRIANGULATE",
        "WAVE",
        "WELD",
        "WIREFRAME",
        "WEIGHTED_NORMAL",
        "UV_PROJECT",
        "UV_WARP",
        "VOLUME_TO_MESH",
        "MESH_TO_VOLUME",
        "OCEAN",
    ]
    settings: dict[str, Any] = Field(default_factory=dict)


def _call(command: str, params: dict[str, Any], changed_objects: list[str] | None = None) -> dict:
    result = get_blender_connection().send_command(command, params)
    resources: list[str] = []
    objects = changed_objects or []
    if isinstance(result, dict):
        result = dict(result)
        objects = result.pop("changed_objects", objects)
        resources = result.pop("changed_resources", resources)
    return ok(result, changed_objects=objects, changed_resources=resources)


@mcp.tool()
async def create_geometry_object(
    ctx: Context,
    name: str,
    geometry: GeometrySpec,
    collection_name: str | None = None,
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> dict:
    """Create one native Blender geometry object from a validated declarative specification."""
    return await asyncio.to_thread(
        _call,
        "create_geometry_object",
        {
            "name": name,
            "geometry": geometry.model_dump(),
            "collection_name": collection_name,
            "location": location,
            "rotation": rotation,
            "scale": scale,
        },
    )


@mcp.tool()
async def set_object_transform(
    ctx: Context,
    object_name: str,
    patch: TransformPatch,
    space: Literal["LOCAL", "WORLD"] = "WORLD",
) -> dict:
    """Set selected transform channels or one complete matrix in explicit local or world space."""
    return await asyncio.to_thread(
        _call,
        "set_object_transform",
        {"object_name": object_name, "patch": patch.model_dump(exclude_none=True), "space": space},
        [object_name],
    )


@mcp.tool()
async def duplicate_or_instance_objects(
    ctx: Context,
    source_object_name: str,
    names: Annotated[list[str], Field(min_length=1, max_length=10_000)],
    transforms: list[InstanceTransform] | None = None,
    mode: Literal["COPY", "LINKED_DATA", "COLLECTION_INSTANCE"] = "LINKED_DATA",
    collection_name: str | None = None,
) -> dict:
    """Create bounded object copies, linked-data copies, or collection instances from one explicit source."""
    if transforms is not None and len(transforms) != len(names):
        raise ValueError("transforms must contain one record per requested name")
    return await asyncio.to_thread(
        _call,
        "duplicate_or_instance_objects",
        {
            "source_object_name": source_object_name,
            "names": names,
            "transforms": [item.model_dump() for item in transforms] if transforms else None,
            "mode": mode,
            "collection_name": collection_name,
        },
    )


@mcp.tool()
async def manage_scene_collections(
    ctx: Context,
    action: Literal["CREATE", "LINK_OBJECTS", "UNLINK_OBJECTS", "SET_VISIBILITY", "REMOVE"],
    collection_name: str,
    object_names: list[str] | None = None,
    parent_collection_name: str | None = None,
    hide_viewport: bool | None = None,
    hide_render: bool | None = None,
    confirm_remove: bool = False,
) -> dict:
    """Manage explicit scene collections without relying on selection or active context."""
    return await asyncio.to_thread(
        _call,
        "manage_scene_collections",
        {key: value for key, value in locals().items() if key != "ctx"},
        object_names,
    )


@mcp.tool()
async def manage_object_hierarchy(
    ctx: Context,
    assignments: Annotated[list[HierarchyAssignment], Field(min_length=1, max_length=1_000)],
    preserve_world_transform: bool = True,
) -> dict:
    """Parent or unparent an explicit batch while optionally preserving each child's world transform."""
    names = [assignment.child_object_name for assignment in assignments]
    return await asyncio.to_thread(
        _call,
        "manage_object_hierarchy",
        {
            "assignments": [item.model_dump() for item in assignments],
            "preserve_world_transform": preserve_world_transform,
        },
        names,
    )


@mcp.tool()
async def manage_object_constraints(
    ctx: Context,
    object_name: str,
    action: Literal["ADD", "PATCH", "REMOVE", "MOVE"],
    constraint: ConstraintSpec,
    position: int | None = Field(default=None, ge=0),
) -> dict:
    """Add, patch, move, or remove one typed object constraint using a bounded property allowlist."""
    return await asyncio.to_thread(
        _call,
        "manage_object_constraints",
        {"object_name": object_name, "action": action, "constraint": constraint.model_dump(), "position": position},
        [object_name],
    )


@mcp.tool()
async def manage_modifiers(
    ctx: Context,
    object_name: str,
    action: Literal["ADD", "PATCH", "MOVE", "REMOVE", "APPLY"],
    modifier: ModifierSpec,
    position: int | None = Field(default=None, ge=0),
    confirm_destructive: bool = False,
) -> dict:
    """Manage one allowlisted non-Geometry-Nodes modifier and report evaluated geometry evidence."""
    if action in {"REMOVE", "APPLY"} and not confirm_destructive:
        raise ValueError("confirm_destructive=True is required for REMOVE or APPLY")
    warnings = [STALE_INDEX_WARNING] if action == "APPLY" else None
    result = await asyncio.to_thread(
        _call,
        "manage_modifiers",
        {
            "object_name": object_name,
            "action": action,
            "modifier": modifier.model_dump(),
            "position": position,
            "confirm_destructive": confirm_destructive,
        },
        [object_name],
    )
    if warnings:
        result["warnings"].extend(warnings)
    return result


@mcp.tool()
async def remove_scene_objects(
    ctx: Context,
    object_names: Annotated[list[str], Field(min_length=1, max_length=1_000)],
    confirm_remove: bool = False,
) -> dict:
    """Remove only the named scene objects after dependency inspection and explicit confirmation."""
    if not confirm_remove:
        raise ValueError("confirm_remove=True is required")
    return await asyncio.to_thread(
        _call,
        "remove_scene_objects",
        {"object_names": object_names, "confirm_remove": confirm_remove},
        object_names,
    )
