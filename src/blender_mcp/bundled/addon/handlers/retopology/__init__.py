"""Blender-main-thread retopology handlers, grouped by topic."""

from .advanced import _AdvancedMixin
from .construction import _ConstructionMixin
from .editing import _EditingMixin
from .production import _ProductionMixin
from .quality import _QualityMixin
from .target import _TargetMixin


class RetopologyHandlersMixin(
    _TargetMixin,
    _ConstructionMixin,
    _EditingMixin,
    _ProductionMixin,
    _QualityMixin,
    _AdvancedMixin,
):
    pass
