import warnings

import scratchattach as sa
from fastmcp import FastMCP

from . import compat

mcp = FastMCP("scratch-unified")

warnings.filterwarnings("ignore", category=sa.LoginDataWarning)
