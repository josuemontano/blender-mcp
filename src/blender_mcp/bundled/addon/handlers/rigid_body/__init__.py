"""Blender handlers for production rigid-body workflows."""

from .animation import RigidBodyAnimationHandlers
from .assemblies import RigidBodyAssemblyHandlers
from .debris import RigidBodyDebrisHandlers
from .delivery import RigidBodyDeliveryHandlers
from .exporting import RigidBodyExportHandlers
from .force_fields import RigidBodyForceFieldHandlers
from .inspection_and_setup import (
    _LAYER_PROFILES as _LAYER_PROFILES,
)
from .inspection_and_setup import (
    RigidBodyInspectionAndSetupHandlers,
)
from .inspection_and_setup import (
    _active_degrees_of_freedom as _active_degrees_of_freedom,
)
from .inspection_and_setup import (
    _constraint_axis_fields as _constraint_axis_fields,
)
from .lifecycle import RigidBodyLifecycleHandlers
from .performance import RigidBodyPerformanceHandlers
from .proxy_rigs import RigidBodyProxyRigHandlers
from .ragdolls import RigidBodyRagdollHandlers
from .simulation import RigidBodySimulationHandlers


class RigidBodyHandlersMixin(
    RigidBodyDebrisHandlers,
    RigidBodyProxyRigHandlers,
    RigidBodyRagdollHandlers,
    RigidBodyExportHandlers,
    RigidBodyPerformanceHandlers,
    RigidBodyLifecycleHandlers,
    RigidBodyAnimationHandlers,
    RigidBodyAssemblyHandlers,
    RigidBodyForceFieldHandlers,
    RigidBodySimulationHandlers,
    RigidBodyDeliveryHandlers,
    RigidBodyInspectionAndSetupHandlers,
):
    """Expose the complete rigid-body command surface to the Blender server."""
