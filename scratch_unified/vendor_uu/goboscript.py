import re
import os, shutil, subprocess
from pathlib import Path
from typing import Optional

from fastmcp.exceptions import ToolError

INSTALL_DOCS = "https://github.com/aspizu/goboscript/raw/refs/heads/main/docs/install.md"

EXTRA_BIN_DIRS = (Path.home() / ".cargo" / "bin", Path("/usr/local/bin"))

TOOLS = {
    "goboscript": {
        "env": "GOBOSCRIPT_BIN",
        "purpose": "compiling a goboscript project into a .sb3",
        "install": (
            "rustup toolchain install nightly && "
            "cargo +nightly install --git https://github.com/aspizu/goboscript"
        ),
    },
    "sb2gs": {
        "env": "SB2GS_BIN",
        "purpose": "decompiling an existing .sb3 project into goboscript source",
        # sb2gs has no --version flag; --help is the only safe probe.
        "version_flag": "--help",
        # sb2gs's README says `--package sb2gs-cli`, which current cargo rejects: the crate has to be positional.
        "install": "cargo install --git https://github.com/aspizu/sb2gs sb2gs-cli",
    },
}

DEFAULT_TIMEOUT = 180


def find(tool: str) -> Optional[str]:
    spec = TOOLS[tool]

    override = os.environ.get(spec["env"])
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None

    found = shutil.which(tool)
    if found: return found

    for directory in EXTRA_BIN_DIRS:
        candidate = directory / tool
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def require(tool: str) -> str:
    found = find(tool)
    if found: return found

    spec = TOOLS[tool]
    raise ToolError(
        f"`{tool}` is not installed, and it is required for {spec['purpose']}.\n\n"
        f"Ask the user to install it, or install it yourself:\n"
        f"    {spec['install']}\n\n"
        f"It needs the Rust toolchain (https://rustup.rs). goboscript specifically "
        f"needs the *nightly* toolchain.\n"
        f"For the full instructions, fetch {INSTALL_DOCS}\n\n"
        f"If it is already installed somewhere unusual, set the {spec['env']} "
        f"environment variable to its full path, or add it to PATH."
    )


def versions() -> dict[str, Optional[str]]:
    report: dict[str, Optional[str]] = {}
    for tool in TOOLS:
        path = find(tool)
        if path is None:
            report[tool] = None
            continue
        try:
            flag = TOOLS[tool].get("version_flag", "--version")
            done = subprocess.run(
                [path, flag], capture_output=True, text=True, timeout=20
            )
            first = ANSI.sub("", done.stdout or done.stderr).strip().splitlines()
            report[tool] = first[0] if first else f"installed at {path}"
        except Exception as error: report[tool] = f"found at {path} but not runnable: {type(error).__name__}"
    return report


def run(
    tool: str,
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess:
    binary = require(tool)
    try:
        return subprocess.run(
            [binary, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(
            f"`{tool} {' '.join(args)}` did not finish within {timeout}s and was "
            f"killed. The project may be very large, or the compiler may be stuck."
        ) from None
    except OSError as error:
        raise ToolError(f"Could not run `{tool}`: {type(error).__name__}: {error}") from error


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def output_of(done: subprocess.CompletedProcess, *, limit: int = 8000) -> str:
    text = "\n".join(part for part in (done.stdout, done.stderr) if part and part.strip())
    text = ANSI.sub("", text).strip()
    if len(text) > limit: text = text[:limit] + f"\n... (truncated, {len(text) - limit} more characters)"
    return text or "(no output)"


## docs

DOCS_BASE = "https://raw.githubusercontent.com/aspizu/goboscript/refs/heads/main/docs/"

# from https://github.com/aspizu/goboscript/blob/main/mkdocs.yml
DOCS_TREE: dict[str, list[tuple[str, str]]] = {
    "Install": [("Install", "install.md")],
    "Getting Started": [
        ("Getting Started", "getting-started/index.md"),
        ("Basic Examples", "getting-started/basic-examples.md"),
    ],
    "Configuration": [("Configuration", "configuration.md")],
    "Language": [
        ("Syntax", "language/syntax.md"),
        ("Sprites", "language/sprites.md"),
        ("Costumes", "language/costumes.md"),
        ("Sounds", "language/sounds.md"),
        ("Variables", "language/variables.md"),
        ("Lists", "language/lists.md"),
        ("Operators", "language/operators.md"),
        ("Control Flow", "language/control-flow.md"),
        ("Hat Blocks", "language/hat-blocks.md"),
        ("Custom Blocks", "language/custom-blocks.md"),
        ("Functions", "language/functions.md"),
        ("Enums", "language/enums.md"),
        ("Structs", "language/structs.md"),
        ("Macros", "language/macros.md"),
    ],
    "Blocks (statements)": [
        ("Motion", "language/blocks/motion.md"),
        ("Looks", "language/blocks/looks.md"),
        ("Sound", "language/blocks/sound.md"),
        ("Events", "language/blocks/events.md"),
        ("Control", "language/blocks/control.md"),
        ("Sensing", "language/blocks/sensing.md"),
        ("Pen", "language/blocks/pen.md"),
        ("Music", "language/blocks/music.md"),
        ("Debugger", "language/blocks/debugger.md"),
    ],
    "Reporters (expressions)": [
        ("Motion", "language/reporters/motion.md"),
        ("Looks", "language/reporters/looks.md"),
        ("Sound", "language/reporters/sound.md"),
        ("Sensing", "language/reporters/sensing.md"),
    ],
    "Recipes": [("Workarounds", "recipes/workarounds.md")],
    "Standard Library": [("Standard Library", "standard-library.md")],
    "Contributing": [("Contributing", "contributing.md")],
    "Editor Integration": [
        ("TurboWarp Desktop", "editor-integration/turbowarp-desktop.md"),
        ("Visual Studio Code", "editor-integration/vscode.md"),
        ("Sublime Text", "editor-integration/sublime-text.md"),
        ("Notepad++", "editor-integration/notepad++.md"),
    ],
}

DOC_PATHS = {path for pages in DOCS_TREE.values() for _, path in pages}

_DOC_CACHE: dict[str, str] = {}


def docs_tree() -> str:
    lines = [
        "goboscript documentation. Call this tool again with `page` set to one "
        "of the paths below to read it.",
        "",
    ]
    for section, pages in DOCS_TREE.items():
        lines.append(f'{section}:')
        for title, path in pages: lines.append(f'    {path:<42} {title}')
        lines.append('')
    lines.append(
        "Most useful when writing code: language/blocks/* for statements, "
        "language/reporters/* for expressions, language/hat-blocks.md for "
        "event hats, and recipes/workarounds.md."
    )
    return '\n'.join(lines)


def docs_page(page: str) -> str:
    import httpx

    wanted = page.strip().lstrip('/')
    if not wanted.endswith('.md'): wanted += '.md'

    if wanted not in DOC_PATHS:
        matches = [p for p in sorted(DOC_PATHS) if wanted.rstrip(".md") in p]
        hint = f" Did you mean: {', '.join(matches[:5])}?" if matches else ''
        raise ToolError(
            f"'{page}' is not a goboscript documentation page. Call this tool "
            f"with no arguments to see the full list.{hint}"
        )

    if wanted in _DOC_CACHE:
        return _DOC_CACHE[wanted]

    url = DOCS_BASE + wanted
    try:
        text = httpx.get(url).text
    except Exception as error:
        raise ToolError(
            f"Could not fetch {url}: {type(error).__name__}: {error}"
        ) from error

    _DOC_CACHE[wanted] = text
    return text