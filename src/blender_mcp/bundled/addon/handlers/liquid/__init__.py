"""Provide production-oriented Mantaflow liquid handlers."""

from .animation import LiquidAnimationHandlers
from .delivery import LiquidDeliveryHandlers
from .delivery import _bounds_volume as _bounds_volume
from .delivery import _validate_axes as _validate_axes
from .force_fields import LiquidForceFieldHandlers
from .guides import LiquidGuideHandlers
from .inspection_and_setup import LiquidInspectionAndSetupHandlers
from .inspection_and_setup import _patch_rna as _patch_rna
from .lifecycle import LiquidLifecycleHandlers
from .mesh_and_materials import LiquidMeshAndMaterialHandlers
from .mesh_and_materials import _expand_viscosity_config as _expand_viscosity_config
from .mesh_and_materials import _particle_role as _particle_role
from .simulation import LiquidSimulationHandlers


class LiquidHandlersMixin(
    LiquidDeliveryHandlers,
    LiquidLifecycleHandlers,
    LiquidSimulationHandlers,
    LiquidMeshAndMaterialHandlers,
    LiquidGuideHandlers,
    LiquidForceFieldHandlers,
    LiquidAnimationHandlers,
    LiquidInspectionAndSetupHandlers,
):
    """Provide production-oriented Mantaflow liquid handlers."""
