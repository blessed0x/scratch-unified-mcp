import hashlib
import json
import re
import shutil
import tempfile
import httpx
from pathlib import Path
from typing import Literal, Optional

from fastmcp.exceptions import ToolError
from typing_extensions import TypedDict

from . import goboscript, sb3 as sb3_util, store, utils
from .prompts import PROJECT_EDITING
from .server import mcp

SCRATCH_EXTENSIONS = frozenset(__import__("scratchattach").editor.extension.Extensions.all_of("code")) # lists all extensions scratch supports
# if any is outside this list it will be considered as turbowarp

Visibility = Literal["public", "private", "unchanged"]

class ProjectSummary(TypedDict):
    path: str
    name: str
    is_active: bool
    published_project_id: Optional[str]
    is_scratch_compatible: bool
    sprites: list[str]
    has_config: bool
    built_sb3: Optional[str]

class DownloadResult(TypedDict):
    project: ProjectSummary
    compiles: Optional[bool]
    diagnostics: Optional[str]
    note: Optional[str]

class BuildResult(TypedDict):
    sb3_path: str
    output: str
    extensions: list[str]
    turbowarp_only_extensions: list[str]
    is_scratch_compatible: bool

class PublishResult(TypedDict):
    project_id: str
    url: str
    created: bool
    assets_uploaded: int
    assets_skipped: int
    visibility: str
    title: Optional[str]
    warnings: list[str]

## helpers

def _sprite_files(directory: Path) -> list[str]: return sorted(p.stem for p in directory.glob("*.gs"))


def _summarise(entry: store.Project) -> ProjectSummary:
    path = Path(entry["path_to_project"])
    sb3 = path / f"{path.name}.sb3"
    return {
        "path": str(path),
        "name": path.name,
        "is_active": entry["path_to_project"] == utils.ACTIVE_PROJECT,
        "published_project_id": entry.get("published_project_id"),
        "is_scratch_compatible": entry.get("is_scratch_compatible", True),
        "sprites": _sprite_files(path) if path.is_dir() else [],
        "has_config": (path / "goboscript.toml").is_file(),
        "built_sb3": str(sb3) if sb3.is_file() else None,
    }


def _resolve(path: str) -> Path: return Path(path).expanduser().resolve()


def _resolve_in(file: str, directory: Path) -> Path:
    given = Path(file).expanduser()
    if given.is_absolute(): return given.resolve()
    inside = directory / given
    return inside.resolve() if inside.exists() else given.resolve()


def _read_sb3(sb3: Path) -> tuple[dict, list[tuple[str, bytes]]]: return sb3_util.read(sb3)


def _actual_visibility(project_id: str) -> Optional[str]:
    import scratchattach as sa
    from scratchattach.utils import exceptions

    try:
        sa.get_project(int(project_id))
        return "public"
    except exceptions.ProjectNotFound: return "private"
    except Exception: return None


def _extensions(project_json: dict) -> tuple[list[str], list[str]]:
    used = [e for e in project_json.get("extensions", []) if isinstance(e, str)]
    return used, [e for e in used if e not in SCRATCH_EXTENSIONS]


## tools


@mcp.tool
def project_editing_guide() -> str:
    """
    Read this BEFORE creating or editing any Scratch project.

    Covers Scratch's hard limits (block counts, asset sizes, clone caps, cloud
    variable rules and so on) and the goboscript language used to write
    projects as text. Scratch projects are not edited as raw project.json here;
    they are written as goboscript `.gs` source and compiled.
    """
    return PROJECT_EDITING


@mcp.tool
def project_check_toolchain() -> dict:
    """
    Report whether the goboscript toolchain is installed and usable.

    Call this first if any other project_* tool complains about a missing
    binary. Never raises, so it is safe to use for diagnosis.
    """
    found = goboscript.versions()
    missing = [name for name, version in found.items() if version is None]
    return {
        "tools": found,
        "missing": missing,
        "ready": not missing,
        "install_docs": goboscript.INSTALL_DOCS,
        "install_commands": {
            name: spec["install"] for name, spec in goboscript.TOOLS.items()
        },
        "note": None
        if not missing
        else (
            f"Missing: {', '.join(missing)}. Install with the commands above "
            f"(needs the Rust toolchain; goboscript needs nightly), then retry."
        ),
    }


@mcp.tool
def project_new(
    path: str,
    published_project_id: Optional[str] = None,
    git: bool = False,
) -> ProjectSummary:
    """
    Create a new goboscript project and make it the active project.

    Scaffolds a project directory with `stage.gs`, `main.gs`, a blank costume
    and a `goboscript.toml`.

    Args:
        path: Directory to create, normally inside your workspace. Must not already exist, or must be empty.
        published_project_id: Existing scratch.mit.edu project id this should publish to. Leave unset for a project that is local-only until you first call `project_save_to_cloud`.
        git: Initialise a git repository in the project. Off by default.
    """
    target = _resolve(path)
    if target.exists() and any(target.iterdir()):
        raise ToolError(
            f"'{target}' already exists and is not empty. Use `project_open` to "
            "adopt an existing project, or pick another path."
        )

    args = ["new", str(target)]
    if not git: args.append("--no-git")

    done = goboscript.run("goboscript", args)
    if done.returncode != 0:
        raise ToolError(
            f"`goboscript new` failed (exit {done.returncode}):\n"
            f"{goboscript.output_of(done)}"
        )

    entry = utils._register_project({
        "path_to_project": str(target),
        "published_project_id": published_project_id,
        "is_scratch_compatible": True,
    })
    utils._set_active_project(entry["path_to_project"])
    utils._try_persist()
    return _summarise(entry)


@mcp.tool
def project_open(path: str, published_project_id: Optional[str] = None) -> ProjectSummary:
    """
    Register an existing goboscript project directory and make it active.

    Args:
        path: The project directory (the one holding goboscript.toml and the .gs sprite files).
        published_project_id: Existing scratch.mit.edu project id this publishes to, if any.
    """
    target = _resolve(path)
    if not target.is_dir(): raise ToolError(f"'{target}' is not a directory.")

    if not _sprite_files(target) and not (target / "goboscript.toml").is_file():
        raise ToolError(
            f"'{target}' has no .gs files and no goboscript.toml, so it does not "
            "look like a goboscript project. Use `project_new` to create one, or "
            "`project_download` to decompile an existing Scratch project."
        )

    existing = utils._find_project(str(target))
    entry = utils._register_project({
        "path_to_project": str(target),
        "published_project_id": (
            published_project_id
            or (existing or {}).get("published_project_id")
        ),
        "is_scratch_compatible": (existing or {}).get("is_scratch_compatible", True),
    })
    utils._set_active_project(entry["path_to_project"])
    utils._try_persist()
    return _summarise(entry)


@mcp.tool
def project_download(
    path: str,
    project_id: Optional[str] = None,
    sb3_path: Optional[str] = None,
    overwrite: bool = False,
    verify: bool = True,
) -> DownloadResult:
    """
    Decompile a Scratch project into editable goboscript source, and make it active.

    Give either `project_id` to pull straight from scratch.mit.edu, or
    `sb3_path` for a local .sb3 file. Decompiling is lossy in layout terms: the
    result is equivalent code, not a byte-identical copy of the original.

    Args:
        path: Directory to write the goboscript project into.
        project_id: Numeric scratch.mit.edu project id to download and decompile.
        sb3_path: Path to a local .sb3 to decompile instead.
        overwrite: Allow writing into a directory that already has contents.
        verify: Compile the decompiled source afterwards and report whether it builds. Decompilation is not always faithful, so this is worth knowing before you start editing.
    """
    if bool(project_id) == bool(sb3_path): raise ToolError("Give exactly one of `project_id` or `sb3_path`.")

    target = _resolve(path)
    if target.exists() and any(target.iterdir()) and not overwrite:
        raise ToolError(
            f"'{target}' already exists and is not empty. Pass overwrite=true to "
            "decompile into it anyway."
        )

    notes: list[str] = []
    scratch_dir = tempfile.TemporaryDirectory(prefix="scratch-mcp-dl-")
    try:
        if project_id:
            if not str(project_id).isdigit(): raise ToolError(f"'{project_id}' is not a numeric project id.")
            source = Path(scratch_dir.name) / f"{project_id}.sb3"
            sb3_util.fetch(utils.active_ses(), int(project_id), source)
        else:
            given = _resolve(sb3_path)
            if not given.is_file(): raise ToolError(f"'{given}' does not exist.")
            if given.suffix != ".sb3": raise ToolError(f"'{given}' must be a .sb3 file.")
            source = Path(scratch_dir.name) / given.name
            shutil.copyfile(given, source)

        notes += sb3_util.rewrite(source)

        args = [str(source), str(target)]
        if overwrite or target.exists(): args.append("--overwrite")
        done = goboscript.run("sb2gs", args)
    finally: scratch_dir.cleanup()

    if done.returncode != 0:
        detail = goboscript.output_of(done)
        hint = ""
        if "404 Not Found" in detail:
            hint = (
                "\n\nThat project id is not publicly shared. sb2gs downloads through "
                "the public API with no login, so unshared projects cannot be "
                "fetched by id. Share it first, or export a .sb3 and pass `sb3_path`."
            )
        elif "missing key" in detail or "sb2gs is cooked" in detail:
            hint = (
                "\n\nsb2gs could not parse this .sb3. Note that it cannot read "
                "goboscript's own output: goboscript omits Stage fields such as "
                "`volume` that sb2gs requires. Decompiling works for projects saved "
                "from the Scratch editor. If this project came from goboscript, edit "
                "its original .gs source instead of decompiling it."
            )
        raise ToolError(f"`sb2gs` failed (exit {done.returncode}):\n{detail}{hint}")

    entry = utils._register_project({
        "path_to_project": str(target),
        "published_project_id": str(project_id) if project_id else None,
        "is_scratch_compatible": True,
    })
    utils._set_active_project(entry["path_to_project"])
    utils._try_persist()

    compiles: Optional[bool] = None
    diagnostics: Optional[str] = None

    if verify:
        check = goboscript.run("goboscript", ["build"], cwd=target)
        compiles = check.returncode == 0
        if not compiles:
            diagnostics = goboscript.output_of(check, limit=4000)
            notes.append(
                "The decompiled goboscript does not compile. Fix the reported "
                "conflicts before building or publishing."
            )

    note = " ".join(notes) if notes else None

    return {
        "project": _summarise(entry),
        "compiles": compiles,
        "diagnostics": diagnostics,
        "note": note,
    }


@mcp.tool
def project_list() -> dict:
    """List the goboscript projects this server knows about."""
    return {
        "active": utils.ACTIVE_PROJECT,
        "projects": [_summarise(entry) for entry in utils.OPEN_PROJECTS],
    }


@mcp.tool
def project_select(path: str) -> ProjectSummary:
    """
    Choose which open project the other project_* tools act on.

    Args:
        path: Path of a project from `project_list`.
    """
    entry = utils._find_project(path)
    if entry is None:
        known = ", ".join(e["path_to_project"] for e in utils.OPEN_PROJECTS) or "none"
        raise ToolError(f"'{path}' is not open. Open projects: {known}.")

    utils._set_active_project(entry["path_to_project"])
    utils._try_persist()
    return _summarise(entry)


@mcp.tool
def project_close(path: Optional[str] = None) -> str:
    """
    Forget a project. Does not delete anything from disk.

    Args:
        path: Project to drop. Defaults to the active project.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")

    utils.OPEN_PROJECTS.remove(entry)
    if utils.ACTIVE_PROJECT == entry["path_to_project"]:
        utils._set_active_project(
            utils.OPEN_PROJECTS[0]["path_to_project"] if utils.OPEN_PROJECTS else None
        )
    utils._try_persist()

    return (
        f"Closed '{entry['path_to_project']}' (files left on disk). "
        + (f"Active project is now '{utils.ACTIVE_PROJECT}'." if utils.ACTIVE_PROJECT
           else "No active project remains.")
    )


@mcp.tool
def project_info(path: Optional[str] = None) -> ProjectSummary:
    """
    Summarise a project: its sprites, publish target and compatibility.

    Args:
        path: Project to describe. Defaults to the active project.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")
    return _summarise(entry)


@mcp.tool
def project_build(path: Optional[str] = None) -> BuildResult:
    """
    Compile the project to .sb3 with goboscript. Use this rather than running
    the goboscript CLI yourself.

    A failed build raises with the compiler's diagnostics passed through
    verbatim -- file, line, column and the offending source -- so fix those and
    call it again.

    Also reports which extensions the project uses and whether any of them are
    TurboWarp-only, which would block publishing to scratch.mit.edu.

    Args:
        path: Project to build. Defaults to the active project.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")

    directory = utils.project_dir(entry)
    done = goboscript.run("goboscript", ["build"], cwd=directory)
    output = goboscript.output_of(done)

    sb3 = directory / f"{directory.name}.sb3"
    if done.returncode != 0:
        raise ToolError(
            f"Build failed (goboscript exit {done.returncode}).\n\n{output}"
        )

    if not sb3.is_file():
        raise ToolError(
            f"goboscript reported success but '{sb3}' does not exist.\n{output}"
        )

    project_json, _ = _read_sb3(sb3)
    used, tw_only = _extensions(project_json)

    entry["is_scratch_compatible"] = not tw_only
    utils._try_persist()

    return {
        "sb3_path": str(sb3),
        "output": output,
        "extensions": used,
        "turbowarp_only_extensions": tw_only,
        "is_scratch_compatible": not tw_only,
    }


@mcp.tool
def project_save_to_cloud(
    path: Optional[str] = None,
    title: Optional[str] = None,
    visibility: Visibility = "unchanged",
    build: bool = True,
    thumbnail: str = "auto",
) -> PublishResult:
    """
    Build the project and upload it to scratch.mit.edu as the active session.

    Creates a new Scratch project the first time, then reuses that id for later
    saves. Uploads costume and sound files as well as the code, since Scratch
    stores assets separately and the project would render broken without them.

    Refuses to upload a project using TurboWarp-only extensions, and names them,
    because scratch.mit.edu will not accept it.

    Args:
        path: Project to publish. Defaults to the active project.
        title: Project title. Used when creating, and renames on later saves. Max 100 characters.
        visibility: "public" to share it, "private" to unshare it, "unchanged" to leave its current state alone.
        build: Compile before uploading. Pass false only if you just built it.
        thumbnail: "auto" (default) renders the Stage backdrop as the thumbnail, since Scratch only generates one in its editor and an uploaded project otherwise shows a grey placeholder. Pass an image path to use your own, or "none" to leave it alone.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")

    session = utils.active_ses()
    directory = utils.project_dir(entry)
    warnings: list[str] = []

    if title is not None and len(title) > 100: raise ToolError(f"Title is {len(title)} characters; Scratch's limit is 100.")

    if build:
        done = goboscript.run("goboscript", ["build"], cwd=directory)
        if done.returncode != 0:
            raise ToolError(
                "Refusing to publish: the project does not compile.\n"
                f"{goboscript.output_of(done)}"
            )

    sb3 = directory / f"{directory.name}.sb3"
    if not sb3.is_file():
        raise ToolError(
            f"No built .sb3 at '{sb3}'. Run `project_build` first, or pass build=true."
        )

    project_json, assets = _read_sb3(sb3)
    used, tw_only = _extensions(project_json)

    if tw_only:
        entry["is_scratch_compatible"] = False
        utils._try_persist()
        raise ToolError(
            "Cannot publish to scratch.mit.edu: this project uses extensions that "
            f"only TurboWarp supports: {', '.join(tw_only)}. "
            "Remove them to publish, or keep the project local / run it in TurboWarp. "
            f"Scratch's own extensions are: {', '.join(sorted(SCRATCH_EXTENSIONS))}."
        )

    entry["is_scratch_compatible"] = True

    uploaded = skipped = 0
    for name, data in assets:
        asset_id, _, ext = name.rpartition(".")
        if not asset_id or not ext:
            warnings.append(f"Skipped oddly-named asset '{name}'.")
            skipped += 1
            continue
        digest = hashlib.md5(data).hexdigest()
        if digest != asset_id:
            warnings.append(
                f"Asset '{name}' does not match its md5 ({digest}); uploaded under "
                "the name the project references."
            )
        try:
            session.upload_asset(data, asset_id=asset_id, file_ext=ext)
            uploaded += 1
        except Exception as error:
            skipped += 1
            warnings.append(f"Failed to upload '{name}': {type(error).__name__}: {error}")

    project_id = entry.get("published_project_id")
    created = False

    if project_id:
        remote = session.connect_project(int(project_id))
        try:
            remote.set_json(project_json)
        except Exception as error:
            raise ToolError(
                f"Could not upload to project {project_id}: "
                f"{type(error).__name__}: {error}. The active session "
                f"('{session.username}') must be the project's author."
            ) from error
    else:
        try:
            remote = session.create_project(
                title=title or directory.name, project_json=project_json
            )
        except Exception as error:
            raise ToolError(
                f"Could not create the project: {type(error).__name__}: {error}"
            ) from error
        created = True
        project_id = str(remote.id)
        entry["published_project_id"] = project_id
        utils._try_persist()

    if title is not None and not created:
        try:
            remote.set_title(title)
        except Exception as error:
            warnings.append(f"Could not set the title: {type(error).__name__}: {error}")

    if thumbnail != "none":
        try:
            if thumbnail == "auto":
                data, problem = _stage_backdrop(sb3)
                if data is None: warnings.append(f"No thumbnail set: {problem}.")
                else: _upload_thumbnail(remote, data)
            else: _upload_thumbnail(remote, _thumbnail_from_file(thumbnail, directory))
        except Exception as error: warnings.append(f"Uploaded, but the thumbnail failed: {type(error).__name__}: {error}")

    if visibility in ("public", "private"):
        try: remote.share() if visibility == "public" else remote.unshare()
        except Exception as error:
            actual = _actual_visibility(project_id)
            if actual == visibility: ...
            else:
                warnings.append(
                    f"Upload succeeded, but could not confirm the change to "
                    f"{visibility}: {type(error).__name__}: {error}. "
                    f"It currently reads as {actual or 'unknown'}, though Scratch's "
                    f"API caches for a few seconds. Check "
                    f"https://scratch.mit.edu/projects/{project_id}/"
                )

    utils._try_persist()

    if created:
        warnings.append(
            "Scratch rate-limits project creation and will ban accounts that spam "
            "it. Later saves reuse this id instead of creating another project."
        )

    return {
        "project_id": str(project_id),
        "url": f"https://scratch.mit.edu/projects/{project_id}/",
        "created": created,
        "assets_uploaded": uploaded,
        "assets_skipped": skipped,
        "visibility": visibility,
        "title": title,
        "warnings": warnings,
    }


## asset tools

# accepted formats by scratch -- anything else causes scratch to fail silently
COSTUME_FORMATS = frozenset({".svg", ".png", ".jpg", ".jpeg"})
SOUND_FORMATS = frozenset({".mp3", ".wav"})
MAX_ASSET_BYTES = 10 * 1024 * 1024

AssetKind = Literal["costume", "sound"]

# costumes "a.svg", "b.svg" as "two"; -- one or more entries, optional rename
_STATEMENT = re.compile(r"^[ \t]*(costumes|sounds)\b(.*?);[ \t]*$", re.M | re.S)
_ENTRY = re.compile(r'"((?:[^"\\]|\\.)*)"(?:\s+as\s+"((?:[^"\\]|\\.)*)")?')


class AssetEntry(TypedDict):
    kind: str
    name: str
    path: str
    exists: bool


class AssetList(TypedDict):
    sprite: str
    costumes: list[AssetEntry]
    sounds: list[AssetEntry]


def _sprite_file(directory: Path, sprite: str) -> Path:
    candidate = directory / f"{sprite}.gs"
    if candidate.is_file():
        return candidate
    available = ", ".join(_sprite_files(directory)) or "none"
    raise ToolError(
        f"Sprite '{sprite}' has no {sprite}.gs in the project root. "
        f"Available sprites: {available}. (The Stage is 'stage'.)"
    )


def _parse_assets(source: str, directory: Path) -> dict[str, list[AssetEntry]]:
    found: dict[str, list[AssetEntry]] = {"costumes": [], "sounds": []}
    for match in _STATEMENT.finditer(source):
        kind = match.group(1)
        for path, alias in _ENTRY.findall(match.group(2)):
            found[kind].append({
                "kind": kind[:-1],
                "name": alias or Path(path).stem,
                "path": path,
                "exists": ("*" in path) or (directory / path).is_file(),
            })
    return found


@mcp.tool
def project_list_assets(sprite: str, path: Optional[str] = None) -> AssetList:
    """
    List the costumes and sounds a sprite declares.

    Args:
        sprite: Sprite name, i.e. its .gs filename without the extension. The Stage is "stage".
        path: Project to inspect. Defaults to the active project.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")
    directory = utils.project_dir(entry)
    parsed = _parse_assets(_sprite_file(directory, sprite).read_text(encoding="utf-8"), directory)
    return {"sprite": sprite, "costumes": parsed["costumes"], "sounds": parsed["sounds"]}


def _add_asset(
    kind: AssetKind,
    sprite: str,
    given: Optional[str],
    svg: Optional[str],
    name: Optional[str],
    project: Optional[str],
) -> AssetEntry:
    entry = utils._find_project(project) if project else utils.active_project()
    if entry is None: raise ToolError(f"'{project}' is not open.")
    directory = utils.project_dir(entry)
    gs = _sprite_file(directory, sprite)

    source = _resolve_in(given, directory) if given else None

    assets = directory / "assets"
    assets.mkdir(exist_ok=True)
    allowed = COSTUME_FORMATS if kind == "costume" else SOUND_FORMATS

    if svg is not None:
        if kind != "costume": raise ToolError("`svg` is only valid for costumes.")
        if not name: raise ToolError("`name` is required when supplying `svg` directly.")
        if "<svg" not in svg: raise ToolError("That does not look like SVG: no <svg> element found.")
        data = svg.encode("utf-8")
        filename = f"{name}.svg"
    else:
        if source is None: raise ToolError("Give either `file` or `svg`.")
        if not source.is_file(): raise ToolError(f"'{source}' does not exist.")
        if source.suffix.lower() not in allowed:
            raise ToolError(
                f"Scratch does not accept '{source.suffix}' for a {kind}. "
                f"Allowed: {', '.join(sorted(allowed))}. Convert it first -- "
                f"an unsupported format makes the project fail to load, silently."
            )
        data = source.read_bytes()
        filename = source.name

    if len(data) > MAX_ASSET_BYTES:
        raise ToolError(
            f"That {kind} is {len(data) / 1e6:.1f} MB; Scratch's per-asset limit "
            f"is 10 MB."
        )

    target_file = assets / filename
    if target_file.exists() and target_file.read_bytes() != data:
        raise ToolError(
            f"'{target_file}' already exists with different contents. Rename the "
            f"file, or pass a different `name`."
        )
    target_file.write_bytes(data)

    display = name or Path(filename).stem
    existing = _parse_assets(gs.read_text(encoding="utf-8"), directory)
    plural = f"{kind}s"
    if any(a["name"] == display for a in existing[plural]):
        raise ToolError(
            f"Sprite '{sprite}' already declares a {kind} named '{display}'. "
            f"Names must be unique within a sprite."
        )

    relative = f"assets/{filename}"
    statement = f'{plural} "{relative}"'
    if display != Path(filename).stem: statement += f' as "{display}"'
    statement += ";\n"

    source_text = gs.read_text(encoding="utf-8")
    matches = list(_STATEMENT.finditer(source_text))
    same_kind = [m for m in matches if m.group(1) == plural]
    if same_kind:
        at = same_kind[-1].end() + 1
        source_text = source_text[:at] + statement + source_text[at:]
    else: source_text = statement + source_text
    gs.write_text(source_text, encoding="utf-8")

    return {"kind": kind, "name": display, "path": relative, "exists": True}


@mcp.tool
def project_add_costume(
    sprite: str,
    file: Optional[str] = None,
    svg: Optional[str] = None,
    name: Optional[str] = None,
    path: Optional[str] = None,
) -> AssetEntry:
    """
    Add a costume to a sprite, copying the file into the project's assets/.

    Give either `file` (an existing .svg/.png/.jpg) or `svg` (SVG markup as a
    string, which needs `name`). Appended last, so it becomes the sprite's
    highest costume number.

    Args:
        sprite: Sprite name, i.e. its .gs filename without the extension. The Stage is "stage".
        file: Path to an image to import.
        svg: SVG markup to write as a new costume. Requires `name`.
        name: Costume name in Scratch. Defaults to the filename without its extension.
        path: Project to modify. Defaults to the active project.
    """
    return _add_asset("costume", sprite, file, svg, name, path)


@mcp.tool
def project_add_sound(
    sprite: str,
    file: str,
    name: Optional[str] = None,
    path: Optional[str] = None,
) -> AssetEntry:
    """
    Add a sound to a sprite, copying the file into the project's assets/.

    Scratch accepts only MP3 and WAV; other formats make the project refuse to
    load, with no warning.

    Args:
        sprite: Sprite name, i.e. its .gs filename without the extension. The Stage is "stage".
        file: Path to an .mp3 or .wav file.
        name: Sound name in Scratch. Defaults to the filename without its extension.
        path: Project to modify. Defaults to the active project.
    """
    return _add_asset("sound", sprite, file, None, name, path)


@mcp.tool
def project_remove_asset(
    sprite: str,
    kind: AssetKind,
    name: str,
    path: Optional[str] = None,
) -> str:
    """
    Remove a costume or sound declaration from a sprite.

    Leaves the file in assets/ in case other sprites use it.

    Args:
        sprite: Sprite name, i.e. its .gs filename without the extension.
        kind: "costume" or "sound".
        name: The costume or sound name to remove, as reported by `project_list_assets`.
        path: Project to modify. Defaults to the active project.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")
    directory = utils.project_dir(entry)
    gs = _sprite_file(directory, sprite)
    plural = f"{kind}s"

    source_text = gs.read_text(encoding="utf-8")
    removed = False

    def rewrite_statement(match: re.Match) -> str:
        nonlocal removed
        if match.group(1) != plural: return match.group(0)
        kept = []
        for asset_path, alias in _ENTRY.findall(match.group(2)):
            if (alias or Path(asset_path).stem) == name and not removed:
                removed = True
                continue
            piece = f'"{asset_path}"'
            if alias: piece += f' as "{alias}"'
            kept.append(piece)
        if not kept: return ""
        return f"{plural} " + ", ".join(kept) + ";"

    source_text = _STATEMENT.sub(rewrite_statement, source_text)

    if not removed:
        declared = _parse_assets(gs.read_text(encoding="utf-8"), directory)[plural]
        known = ", ".join(a["name"] for a in declared) or "none"
        raise ToolError(f"Sprite '{sprite}' declares no {kind} named '{name}'. Declared: {known}.")

    gs.write_text(source_text, encoding="utf-8")
    return f"Removed {kind} '{name}' from '{sprite}'. The file in assets/ was kept."


## inspection

class TargetSummary(TypedDict):
    name: str
    is_stage: bool
    layer_order: Optional[int]
    blocks: int
    scripts: int
    costumes: list[str]
    sounds: list[str]
    variables: list[str]
    lists: list[str]


class AssetSummary(TypedDict):
    filename: str
    kb: float
    used_by: list[str]


class ProjectSummary2(TypedDict):
    project: ProjectSummary
    sb3_path: str
    sb3_kb: float
    stale: bool
    total_blocks: int
    extensions: list[str]
    turbowarp_only_extensions: list[str]
    global_variables: list[str]
    global_lists: list[str]
    targets: list[TargetSummary]
    assets: list[AssetSummary]
    warnings: list[str]


@mcp.tool
def project_summary(path: Optional[str] = None) -> ProjectSummary2:
    """
    Inspect what a project actually compiled to.

    Reads the built .sb3 and reports every target with its block and script
    counts, costumes, sounds, variables and lists, plus the assets embedded and
    which sprites use them. Use it to verify a build did what you intended --
    that a costume really got attached, that layer order is right, that a sound
    is present and not oversized.

    Requires a build; run `project_build` first. Warns if the source has been
    edited since the .sb3 was written.

    Args:
        path: Project to inspect. Defaults to the active project.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")

    directory = utils.project_dir(entry)
    sb3 = directory / f"{directory.name}.sb3"
    if not sb3.is_file():
        raise ToolError(f"No build at '{sb3}'. Run `project_build` first.")

    project_json, assets = _read_sb3(sb3)
    used, tw_only = _extensions(project_json)
    warnings: list[str] = []

    built_at = sb3.stat().st_mtime
    newer = [
        p.name
        for p in list(directory.glob("*.gs")) + list((directory / "assets").glob("*"))
        if p.is_file() and p.stat().st_mtime > built_at
    ]
    if newer:
        warnings.append(
            f"Source is newer than the build ({', '.join(sorted(newer)[:6])}"
            f"{'...' if len(newer) > 6 else ''}). Re-run `project_build`."
        )

    stage = next((t for t in project_json.get("targets", []) if t.get("isStage")), {})

    targets: list[TargetSummary] = []
    total_blocks = 0
    asset_users: dict[str, list[str]] = {}

    for target in project_json.get("targets", []):
        blocks = target.get("blocks") or {}

        scripts = sum(
            1 for b in blocks.values()
            if isinstance(b, dict) and b.get("topLevel")
        )
        total_blocks += len(blocks)

        for asset in list(target.get("costumes") or []) + list(target.get("sounds") or []):
            md5ext = asset.get("md5ext")
            if md5ext:
                asset_users.setdefault(md5ext, []).append(target.get("name", "?"))

        targets.append({
            "name": target.get("name", "?"),
            "is_stage": bool(target.get("isStage")),
            "layer_order": target.get("layerOrder"),
            "blocks": len(blocks),
            "scripts": scripts,
            "costumes": [c.get("name") for c in target.get("costumes") or []],
            "sounds": [s.get("name") for s in target.get("sounds") or []],
            "variables": sorted(v[0] for v in (target.get("variables") or {}).values() if v),
            "lists": sorted(v[0] for v in (target.get("lists") or {}).values() if v),
        })

    targets.sort(key=lambda t: (not t["is_stage"], t["layer_order"] or 0))

    asset_list: list[AssetSummary] = []
    for name, data in sorted(assets, key=lambda a: -len(a[1])):
        kb = round(len(data) / 1024, 1)
        users = asset_users.get(name, [])
        if not users: warnings.append(f"Asset '{name}' is in the .sb3 but no target uses it.")
        if len(data) > MAX_ASSET_BYTES:
            warnings.append(f"Asset '{name}' is {kb / 1024:.1f} MB, over Scratch's 10 MB limit.")
        asset_list.append({"filename": name, "kb": kb, "used_by": sorted(set(users))})

    if tw_only:
        warnings.append(
            f"Uses TurboWarp-only extensions ({', '.join(tw_only)}); "
            f"scratch.mit.edu will refuse this project."
        )

    json_kb = len(json.dumps(project_json).encode()) / 1024
    if json_kb > 5 * 1024: warnings.append(f"project.json is {json_kb / 1024:.1f} MB, over Scratch's 5 MB limit.")

    return {
        "project": _summarise(entry),
        "sb3_path": str(sb3),
        "sb3_kb": round(sb3.stat().st_size / 1024, 1),
        "stale": bool(newer),
        "total_blocks": total_blocks,
        "extensions": used,
        "turbowarp_only_extensions": tw_only,
        "global_variables": sorted(v[0] for v in (stage.get("variables") or {}).values() if v),
        "global_lists": sorted(v[0] for v in (stage.get("lists") or {}).values() if v),
        "targets": targets,
        "assets": asset_list,
        "warnings": warnings,
    }


@mcp.tool
def project_goboscript_docs_help(page: Optional[str] = None) -> str:
    """
    Read the goboscript language documentation.

    Call with no arguments to get the index of every documentation page. Call
    with `page` set to one of those paths to get that page as raw markdown.

    Use this whenever you are unsure of a block or reporter name -- goboscript
    names differ from the Scratch block text (for example `switch_costume`,
    `change_x`, `touching("sprite")`, `clone`, `set_ghost_effect`) and guessing
    wastes a build cycle.

    Args:
        page: A documentation path from the index, e.g. "language/blocks/motion.md". Omit to list everything.
    """
    if page is None: return goboscript.docs_tree()
    return goboscript.docs_page(page)

## thumbnails

THUMBNAIL_SIZE = (480, 360)
THUMBNAIL_RASTER = frozenset({"png", "jpeg", "gif"})
MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024

RASTERISERS = (
    ("inkscape", lambda src, out, w, h: ["inkscape", str(src), "-w", str(w), "-h", str(h), "-o", str(out)]),
    ("rsvg-convert", lambda src, out, w, h: ["rsvg-convert", "-w", str(w), "-h", str(h), "-o", str(out), str(src)]),
    ("magick", lambda src, out, w, h: ["magick", "-background", "none", str(src), "-resize", f"{w}x{h}!", str(out)]),
    ("convert", lambda src, out, w, h: ["convert", "-background", "none", str(src), "-resize", f"{w}x{h}!", str(out)]),
)


def _rasterise_svg(data: bytes) -> Optional[bytes]:
    import shutil
    import subprocess

    with tempfile.TemporaryDirectory(prefix="scratch-mcp-thumb-") as work:
        src = Path(work) / "in.svg"
        out = Path(work) / "out.png"
        src.write_bytes(data)
        for binary, build in RASTERISERS:
            if shutil.which(binary) is None: continue
            try: done = subprocess.run(build(src, out, *THUMBNAIL_SIZE), capture_output=True, timeout=60)
            except Exception: continue
            if done.returncode == 0 and out.is_file() and out.stat().st_size: return out.read_bytes()
    return None


def _stage_backdrop(sb3: Path) -> tuple[Optional[bytes], Optional[str]]:

    project_json, assets = sb3_util.read(sb3)
    stage = next((t for t in project_json.get("targets", []) if t.get("isStage")), None)
    costumes = (stage or {}).get("costumes") or []

    if not costumes: return None, "the Stage has no backdrop to use as a thumbnail"

    wanted = costumes[0].get("md5ext")
    data = next((body for name, body in assets if name == wanted), None)
    if data is None: return None, f"backdrop '{wanted}' is missing from the build"

    kind, _, _ = utils.image_size(data)

    if kind in THUMBNAIL_RASTER: return data, None

    if kind == "svg":
        rendered = _rasterise_svg(data)
        if rendered is None: return None, "the backdrop is an SVG and no rasteriser is installed (inkscape, rsvg-convert or magick), so it cannot be converted; pass an image to `thumbnail` instead"
        return rendered, None
    
    return None, f"the backdrop is an unsupported format ({kind})"


def _upload_thumbnail(remote, data: bytes) -> None:
    if len(data) > MAX_THUMBNAIL_BYTES: raise ToolError(f"Thumbnail is {len(data) / 1e6:.1f} MB; keep it under 5 MB.")

    response = httpx.post(
        f"https://scratch.mit.edu/internalapi/project/thumbnail/{remote.id}/set/",
        data=data,
        headers=remote._headers,
        cookies=remote._cookies,
        timeout=60,
    )
    body: dict = {}

    try:
        parsed = response.json()
        if isinstance(parsed, dict): body = parsed
    except ValueError: pass

    if response.status_code != 200 or body.get("status") != "ok":
        raise ToolError(
            f"Scratch rejected the thumbnail (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )


def _thumbnail_from_file(file: str, directory: Optional[Path] = None) -> bytes:
    source = _resolve_in(file, directory) if directory else _resolve(file)
    if not source.is_file(): raise ToolError(f"'{source}' does not exist.")
    data = source.read_bytes()
    kind, _, _ = utils.image_size(data)
    if kind == "svg":
        rendered = _rasterise_svg(data)

        if rendered is None:
            raise ToolError(
                f"'{source.name}' is an SVG and no rasteriser (inkscape, "
                f"rsvg-convert, magick) is installed to convert it. Export it to "
                f"PNG first."
            )
        
        return rendered
    if kind not in THUMBNAIL_RASTER: raise ToolError(f"'{source.name}' is not a PNG, JPEG, GIF or SVG (detected: {kind}).")
    return data


@mcp.tool
def project_set_thumbnail(
    file: Optional[str] = None, path: Optional[str] = None
) -> dict:
    """
    Set the thumbnail of a published project.

    Scratch generates thumbnails in its editor, so a project uploaded through
    this server keeps the grey placeholder until one is set. `project_save_to_cloud`
    does this automatically; use this tool to change it afterwards.

    With no `file`, the Stage's first backdrop from the latest build is used,
    converted from SVG if a rasteriser is available. 480x360 suits Scratch best.

    Args:
        file: Image to upload (PNG, JPEG, GIF, or SVG if a rasteriser is installed). Omit to use the project's backdrop.
        path: Project to act on. Defaults to the active project.
    """
    entry = utils._find_project(path) if path else utils.active_project()
    if entry is None: raise ToolError(f"'{path}' is not open.")

    project_id = entry.get("published_project_id")
    if not project_id: raise ToolError("This project has not been published yet, so it has no thumbnail to set. Run `project_save_to_cloud` first.")

    directory = utils.project_dir(entry)
    if file: data, source = _thumbnail_from_file(file, directory), file
    else:
        sb3 = directory / f"{directory.name}.sb3"
        if not sb3.is_file(): raise ToolError(f"No build at '{sb3}'. Run `project_build` first.")
        data, problem = _stage_backdrop(sb3)
        if data is None: raise ToolError(f"Could not build a thumbnail: {problem}.")
        source = "the project's backdrop"

    remote = utils.active_ses().connect_project(int(project_id))
    _upload_thumbnail(remote, data)

    kind, width, height = utils.image_size(data)
    return {
        "project_id": str(project_id),
        "url": f"https://scratch.mit.edu/projects/{project_id}/",
        "source": source,
        "format": kind,
        "width": width,
        "height": height,
        "kilobytes": round(len(data) / 1024, 1),
        "note": "Scratch's CDN caches thumbnails, so the new one can take a moment to appear.",
    }