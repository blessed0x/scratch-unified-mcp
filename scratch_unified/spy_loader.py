"""Load ScratchPy Studio headless core without a display.

`scratchpy_studio.py` imports tkinter at module top (lines 48-50) but all
pure logic (Block/Project/SpyFile, importer, codegen, MCPServer tools) works
headless. tkinter exists on most systems anyway; this loader stubs it only as
a fallback so `--mcp`-style usage never needs a display server.
"""
import importlib.util
import sys
import types
from pathlib import Path

# Vendored copy (MIT, ZDStudios/scratchpy-studio — see vendor_spy/LICENSE).
# Upstream is gitignored/read-only; edits land here, never there.
SPY_PATH = Path(__file__).resolve().parent / "vendor_spy" / "scratchpy_studio.py"

_loaded = None


def _stub_tkinter():
    mod = types.ModuleType("tkinter")
    for sub in ("filedialog", "messagebox", "simpledialog", "ttk", "font"):
        setattr(mod, sub, types.ModuleType("tkinter." + sub))
        sys.modules["tkinter." + sub] = sys.modules.get("tkinter." + sub, getattr(mod, sub))
    sys.modules.setdefault("tkinter", mod)


def load_spy():
    """Import and return the scratchpy_studio module (cached)."""
    global _loaded
    if _loaded is not None:
        return _loaded
    try:
        import tkinter  # noqa: F401
    except Exception:
        _stub_tkinter()
    spec = importlib.util.spec_from_file_location("scratchpy_studio", SPY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scratchpy_studio"] = mod
    spec.loader.exec_module(mod)
    _loaded = mod
    return mod
