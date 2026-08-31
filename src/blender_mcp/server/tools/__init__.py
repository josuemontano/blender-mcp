"""Import every tool submodule for its @mcp.tool() registration side effect."""

# Registration order is intentional: the documentation pass must run last.
# ruff: file-ignore[unsorted-imports]

from . import camera as camera
from . import character_rigging as character_rigging
from . import cloth as cloth
from . import core as core
from . import execute as execute
from . import lighting as lighting
from . import liquid as liquid
from . import mesh as mesh
from . import model as model
from . import nd as nd
from . import polyhaven as polyhaven
from . import retopology as retopology
from . import rigid_body as rigid_body
from . import sketchfab as sketchfab
from . import viewport as viewport
from . import _documentation as _documentation
