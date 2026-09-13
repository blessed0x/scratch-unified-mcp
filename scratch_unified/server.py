"""Unified Scratch MCP server app.

Merges three upstreams into one stdio MCP server:
  social_*, project_*  - vendored uukelele/scratch-mcp (scratchattach+goboscript)
  spy_*                - ScratchPy Studio headless core (blocks <-> real Python)
  sb3_*, sb3_git_*     - scratch4js Node sidecar proxy (lazy, degrades gracefully)

Import order matters: vendor_uu/server.py owns the single FastMCP app object
(vendored modules do `from .server import mcp`). This module re-exports it,
registers the extra tool families, and serves.
"""
from .vendor_uu.server import mcp

__all__ = ["mcp", "main"]

from .vendor_uu import projects, social  # noqa: F401 (register social_*/project_*)
from . import sb3_extra  # noqa: F401 (register sb3_git_*/cloud/studio tools)
from .node_bridge import register_sb3_tools
from .spy_tools import register_spy_tools

register_sb3_tools(mcp)
register_spy_tools(mcp)


def main(argv=None):
    """Restore persisted Scratch sessions and serve over stdio.

    `scratch-unified --help` / `--version` answer without starting the
    server (used by install.sh verification and by users sanity-checking
    PATH). Everything else serves MCP over stdio.
    """
    import sys

    from . import __version__
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        print(
            "scratch-unified %s — headless Scratch VM over MCP (stdio).\n"
            "\n"
            "Usage: scratch-unified [--help] [--version] [--list-tools]\n"
            "\n"
            "Configure your MCP client to run this command over stdio; no\n"
            "arguments are needed in normal use. See README.md Quickstart." % __version__
        )
        return
    if "--version" in args or "-V" in args:
        print("scratch-unified %s" % __version__)
        return
    if "--list-tools" in args:
        import asyncio

        names = sorted(t.name for t in asyncio.run(mcp.list_tools()))
        print("\n".join(names))
        return
    from .vendor_uu import utils

    utils._restore()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
