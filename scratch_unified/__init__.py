"""Unified Scratch MCP server: uukelele/scratch-mcp + scratch4js + scratchpy-studio."""
from pathlib import Path

__version__ = "1.0.0"

ROOT = Path(__file__).resolve().parent.parent
NODE_SERVER = ROOT / "scratch_unified" / "sidecar" / "src" / "index.js"
SPY_FILE = ROOT / "scratch_unified" / "vendor_spy" / "scratchpy_studio.py"
