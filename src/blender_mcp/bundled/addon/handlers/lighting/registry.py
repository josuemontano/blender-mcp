"""Compose the lighting handler groups exposed by the Blender add-on server."""

from .construction import LightConstructionHandlers
from .environment import EnvironmentLightingHandlers
from .inspection import LightingInspectionHandlers
from .rendering import LightingRenderHandlers


class LightingHandlers(
    LightingInspectionHandlers,
    LightConstructionHandlers,
    EnvironmentLightingHandlers,
    LightingRenderHandlers,
):
    """Provide the complete production lighting command surface to the add-on server."""
