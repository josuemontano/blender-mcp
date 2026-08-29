"""`blender-mcp` CLI entrypoint: install-addon/addon-paths subcommands, or mcp.run()."""

import logging
import sys

from ..addon_manager import run_cli as run_addon_cli
from .app import mcp

logger = logging.getLogger("BlenderMCPServer")


def main():
    """Run the MCP server, or addon install CLI subcommands."""
    if len(sys.argv) > 1 and sys.argv[1] in {
        "install-addon",
        "addon-paths",
        "-h",
        "--help",
    }:
        code = run_addon_cli(sys.argv[1:])
        if code >= 0:
            raise SystemExit(code)

    # When run by hand (stdin is a TTY) the server appears to "hang" while it
    # silently waits for an MCP client; log a hint so that state is obvious.
    # Launched by a client, stdin is a pipe so this is skipped, and logging goes
    # to stderr, never to the stdio protocol on stdout.
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError):
        interactive = False
    if interactive:
        logger.info(
            "BlenderMCP is an MCP server and is meant to be launched by your MCP "
            "client (Claude Desktop, Cursor, VS Code, ...), not run by hand. "
            "It will now wait silently for a client on stdin -- that is normal, "
            "not a hang. Press Ctrl-C to exit. "
            "Setup guide: https://github.com/ahujasid/blender-mcp#installation "
            "(if the addon is outdated this logs how to update it: blender-mcp install-addon)"
        )
    mcp.run()


if __name__ == "__main__":
    main()
