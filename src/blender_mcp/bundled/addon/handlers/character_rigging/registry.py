"""Composition root for the character-rigging handler surface."""

from .controls import ControlRigHandlersMixin
from .deformation import DeformationHandlersMixin
from .foundation import FoundationHandlersMixin
from .posing import PoseAnimationHandlersMixin


class CharacterRiggingHandlersMixin(
    FoundationHandlersMixin,
    DeformationHandlersMixin,
    ControlRigHandlersMixin,
    PoseAnimationHandlersMixin,
):
    """Provide the complete structured character-rigging command surface."""
