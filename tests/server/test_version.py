"""MCP server identity regression tests."""

from blender_mcp import __version__
from blender_mcp.server import mcp


def test_mcp_server_advertises_package_version() -> None:
    """The initialize response must identify this package, not the MCP SDK."""
    initialization = mcp._mcp_server.create_initialization_options()

    assert initialization.server_name == "BlenderMCP"
    assert initialization.server_version == __version__
