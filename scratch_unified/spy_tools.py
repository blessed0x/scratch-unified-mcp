"""Native spy_* tools: ScratchPy Studio blocks<->Python core, via spy_loader.

The 14 upstream tool names are re-exposed with a `spy_` prefix to avoid
colliding with the project_* / sb3_* families. One shared MCPServer instance
holds the open .spy project; spy_open_project switches it.
"""
import os
import tempfile

from .spy_loader import load_spy

_spy = load_spy()
_SPY_DIR = os.path.join(tempfile.gettempdir(), "scratch-unified-spy")
os.makedirs(_SPY_DIR, exist_ok=True)
_server = _spy.MCPServer(os.path.join(_SPY_DIR, "default.spy"))


def spy_open_project(path: str) -> str:
    """Open (or create) a ScratchPy .spy project file; all other spy_* tools act on it."""
    return _server.call("open_project", {"path": path})


def spy_project_overview() -> str:
    """What is in the .spy project: tabs, variables, lists, custom blocks, package packs."""
    return _server.call("project_overview", {})


def spy_read_blocks(file: str = "") -> str:
    """Readable outline of every script in a tab."""
    return _server.call("read_blocks", {"file": file} if file else {})


def spy_read_code(file: str = "") -> str:
    """The Python a tab's blocks generate."""
    return _server.call("read_code", {"file": file} if file else {})


def spy_write_python(source: str, file: str = "main", replace: bool = True) -> str:
    """THE MAIN BUILD TOOL: give ordinary Python, it becomes Scratch blocks in a tab."""
    return _server.call("write_python", {"source": source, "file": file, "replace": replace})


def spy_import_python_file(path: str) -> str:
    """Turn an existing .py file on disk into blocks."""
    return _server.call("import_python_file", {"path": path})


def spy_delete_file(file: str) -> str:
    """Remove a tab from the project."""
    return _server.call("delete_file", {"file": file})


def spy_set_variable(name: str, value: str = "0", kind: str = "variable") -> str:
    """Create a variable or list, or change its starting value."""
    return _server.call("set_variable", {"name": name, "value": value, "kind": kind})


def spy_run(file: str = "", stdin: str = "", timeout: float = 30) -> str:
    """Run a tab's generated Python and return what it printed."""
    args: dict = {"timeout": timeout}
    if file:
        args["file"] = file
    if stdin:
        args["stdin"] = stdin
    return _server.call("run", args)


def spy_list_block_types(category: str = "") -> str:
    """Every kind of block ScratchPy knows, with the Python each one produces."""
    return _server.call("list_block_types", {"category": category} if category else {})


def spy_list_packages() -> str:
    """Python packages installed in the environment ScratchPy uses."""
    return _server.call("list_packages", {})


def spy_install_package(name: str) -> str:
    """pip install a package and turn it into blocks."""
    return _server.call("install_package", {"name": name})


def spy_add_package_blocks(module: str) -> str:
    """Make blocks for an already-installed module (stdlib modules work too)."""
    return _server.call("add_package_blocks", {"module": module})


def spy_remove_package_blocks(module: str) -> str:
    """Take a package's blocks back out of the project."""
    return _server.call("remove_package_blocks", {"module": module})


SPY_TOOL_DEFS = [
    spy_open_project, spy_project_overview, spy_read_blocks, spy_read_code,
    spy_write_python, spy_import_python_file, spy_delete_file, spy_set_variable,
    spy_run, spy_list_block_types, spy_list_packages, spy_install_package,
    spy_add_package_blocks, spy_remove_package_blocks,
]


def register_spy_tools(mcp):
    for fn in SPY_TOOL_DEFS:
        mcp.tool(fn)
