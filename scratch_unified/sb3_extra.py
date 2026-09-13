"""Extra sb3_* tools with no equivalent in social_*: git-sb3 style diff plus cloud/studio helpers.

git-sb3 tools are pure-Python (zipfile + json) so they work without Node.
Cloud/studio tools use scratchattach where available and degrade with a clear
message otherwise.
"""
import json
import zipfile
from pathlib import Path

from .vendor_uu.server import mcp


def _resolve(p: str) -> Path:
    return Path(p).expanduser().resolve()


@mcp.tool
def sb3_git_unpack(sb3_path: str, out_dir: str) -> str:
    """Unpack an .sb3 into a diffable directory (project.json + assets/)."""
    src = _resolve(sb3_path)
    dest = _resolve(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dest)
        names = zf.namelist()
    return "Unpacked %d files to %s (project.json %s)." % (
        len(names), dest, "present" if "project.json" in names else "MISSING")


@mcp.tool
def sb3_git_pack(project_dir: str, sb3_path: str) -> str:
    """Pack a diffable directory back into an .sb3."""
    src = _resolve(project_dir)
    dest = _resolve(sb3_path)
    pj = src / "project.json"
    if not pj.is_file():
        raise ValueError("No project.json in %s; unpack an .sb3 first." % src)
    # Validate JSON before packing.
    json.loads(pj.read_text(encoding="utf-8"))
    files = sorted(p for p in src.rglob("*") if p.is_file())
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(src).as_posix())
    return "Packed %d files into %s." % (len(files), dest)


@mcp.tool
def sb3_git_diff(project_dir: str) -> str:
    """Summarise an unpacked project dir: targets, block counts, assets."""
    src = _resolve(project_dir)
    pj = src / "project.json"
    if not pj.is_file():
        raise ValueError("No project.json in %s." % src)
    data = json.loads(pj.read_text(encoding="utf-8"))
    lines = []
    for t in data.get("targets", []):
        blocks = t.get("blocks", {})
        lines.append("%s: %d blocks, %d costumes, %d sounds" % (
            t.get("name", "?"), len(blocks),
            len(t.get("costumes", [])), len(t.get("sounds", []))))
    assets = [p for p in src.iterdir() if p.is_file() and p.name != "project.json"]
    lines.append("%d loose asset files." % len(assets))
    return "\n".join(lines) or "Empty project."


@mcp.tool
def sb3_studio_info(studio_id: str) -> str:
    """Fetch a Scratch studio's title, description, and stats."""
    from .vendor_uu import utils

    ses = utils.maybe_ses()
    studio = ses.connect_studio(studio_id) if ses is not None else None
    if studio is None:
        import scratchattach as sa

        studio = sa.get_studio(studio_id)
    try:
        studio.update()
    except Exception:
        pass
    return json.dumps({
        "id": studio.id,
        "title": getattr(studio, "title", None),
        "description": (getattr(studio, "description", "") or "")[:500],
        "stats": getattr(studio, "stats", None),
    }, default=str)[:4000]


@mcp.tool
def sb3_remixes(project_id: str) -> str:
    """List remix lineage info for a project (id, title, author)."""
    import scratchattach as sa

    project = sa.get_project(project_id)
    out = []
    try:
        for r in project.remixes() or []:
            out.append({"id": getattr(r, "id", None), "title": getattr(r, "title", None)})
    except Exception as exc:
        return "Could not fetch remixes: %s" % exc
    return json.dumps(out[:50], default=str)


@mcp.tool
def sb3_favorites(username: str) -> str:
    """List a user's favorited projects (id + title)."""
    import scratchattach as sa

    user = sa.get_user(username)
    out = []
    try:
        for p in user.favorites() or []:
            out.append({"id": getattr(p, "id", None), "title": getattr(p, "title", None)})
    except Exception as exc:
        return "Could not fetch favorites: %s" % exc
    return json.dumps(out[:50], default=str)


@mcp.tool
def sb3_cloud_get_vars(project_id: str) -> str:
    """Read current cloud variable values for a project."""
    from .vendor_uu import utils

    ses = utils.maybe_ses()
    if ses is None:
        raise ValueError("No active Scratch session. Call social_connect_session first.")
    cloud = ses.connect_cloud(project_id)
    try:
        data = cloud.get_all_vars() if hasattr(cloud, "get_all_vars") else cloud.logs()
    except Exception as exc:
        return "Cloud read failed: %s" % exc
    return json.dumps(data, default=str)[:8000]


@mcp.tool
def sb3_cloud_set_var(project_id: str, name: str, value: str) -> str:
    """Set a cloud variable value."""
    from .vendor_uu import utils

    ses = utils.maybe_ses()
    if ses is None:
        raise ValueError("No active Scratch session. Call social_connect_session first.")
    cloud = ses.connect_cloud(project_id)
    try:
        cloud.set_var(name, value)
    except Exception as exc:
        return "Cloud write failed: %s" % exc
    return "Set cloud var %r to %r on project %s." % (name, value, project_id)


@mcp.tool
def sb3_cloud_logs(project_id: str, limit: int = 20) -> str:
    """Recent cloud activity log for a project."""
    from .vendor_uu import utils

    ses = utils.maybe_ses()
    if ses is None:
        raise ValueError("No active Scratch session. Call social_connect_session first.")
    cloud = ses.connect_cloud(project_id)
    try:
        logs = cloud.logs(limit=limit) if hasattr(cloud, "logs") else cloud.get_all_vars()
    except Exception as exc:
        return "Cloud logs failed: %s" % exc
    return json.dumps(logs, default=str)[:8000]
