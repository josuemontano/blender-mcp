"""Blender-main-thread camera handlers, grouped by topic."""

from .animation import _AnimationMixin
from .core import _CoreMixin
from .inspection import _InspectionMixin
from .rigs import _RigsMixin
from .shots import _ShotsMixin
from .targeting import _TargetingMixin


class CameraHandlersMixin(
    _CoreMixin,
    _TargetingMixin,
    _RigsMixin,
    _AnimationMixin,
    _ShotsMixin,
    _InspectionMixin,
):
    pass
