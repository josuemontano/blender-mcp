"""Composition root for the structured Geometry Nodes handler surface."""

from .assets import GeometryNodesAssetHandlersMixin
from .attributes_and_instances import GeometryNodesAttributeInstanceHandlersMixin
from .authoring import GeometryNodesAuthoringHandlersMixin
from .inspection import GeometryNodesInspectionHandlersMixin
from .modifiers import GeometryNodesModifierHandlersMixin
from .workflows import GeometryNodesWorkflowHandlersMixin


class GeometryNodesHandlersMixin(
    GeometryNodesInspectionHandlersMixin,
    GeometryNodesAuthoringHandlersMixin,
    GeometryNodesModifierHandlersMixin,
    GeometryNodesWorkflowHandlersMixin,
    GeometryNodesAttributeInstanceHandlersMixin,
    GeometryNodesAssetHandlersMixin,
):
    """Provide the complete structured Geometry Nodes command surface."""
