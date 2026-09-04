"""Composition root for the structured Geometry Nodes handler surface."""

from .assets import GeometryNodesAssetHandlersMixin
from .attributes_and_instances import GeometryNodesAttributeInstanceHandlersMixin
from .authoring import GeometryNodesAuthoringHandlersMixin
from .bakes import GeometryNodesBakeHandlersMixin
from .delivery import GeometryNodesDeliveryHandlersMixin
from .inspection import GeometryNodesInspectionHandlersMixin
from .modifiers import GeometryNodesModifierHandlersMixin
from .performance import GeometryNodesPerformanceHandlersMixin
from .workflows import GeometryNodesWorkflowHandlersMixin
from .zones import GeometryNodesZoneHandlersMixin


class GeometryNodesHandlersMixin(
    GeometryNodesInspectionHandlersMixin,
    GeometryNodesAuthoringHandlersMixin,
    GeometryNodesModifierHandlersMixin,
    GeometryNodesWorkflowHandlersMixin,
    GeometryNodesAttributeInstanceHandlersMixin,
    GeometryNodesAssetHandlersMixin,
    GeometryNodesZoneHandlersMixin,
    GeometryNodesBakeHandlersMixin,
    GeometryNodesDeliveryHandlersMixin,
    GeometryNodesPerformanceHandlersMixin,
):
    """Provide the complete structured Geometry Nodes command surface."""
