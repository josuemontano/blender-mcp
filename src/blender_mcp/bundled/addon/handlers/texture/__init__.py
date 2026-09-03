"""Blender-main-thread PBR texturing handlers grouped by workflow."""

from .baking import TextureBakingHandlers
from .images import TextureImageHandlers
from .materials import TextureMaterialHandlers
from .previews import TexturePreviewHandlers
from .uv import TextureUVHandlers
from .validation import TextureValidationHandlers


class TextureHandlers(
    TextureMaterialHandlers,
    TextureImageHandlers,
    TextureUVHandlers,
    TextureBakingHandlers,
    TexturePreviewHandlers,
    TextureValidationHandlers,
):
    """Provide material, image, UV, baking, preview, and validation commands."""
