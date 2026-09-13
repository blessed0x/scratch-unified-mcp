from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Union

import httpx
import scratchattach as sa
from fastmcp.exceptions import ToolError

# pydantic (+ FastMCP's output schemas) rejects `typing.TypedDict` @ python < 3.12 and demands the `typing_extensions` one.
from typing_extensions import TypedDict

from . import store

BROWSERS = {
    "autodetect": sa.Browser.ANY,
    "firefox": sa.Browser.FIREFOX,
    "chrome": sa.Browser.CHROME,
    "edge": sa.Browser.EDGE,
    "safari": sa.Browser.SAFARI,
    "chromium": sa.Browser.CHROMIUM,
    "vivaldi": sa.Browser.VIVALDI,
}

BrowserName = Literal[
    "autodetect", "firefox", "chrome", "edge", "safari", "chromium", "vivaldi"
]

CommentSource = Literal["project", "profile", "studio"]

# username: session
SESSIONS: dict[str, sa.Session] = {}

# username of active session
ACTIVE: str | None = None

# usernames from disk store
PERSISTED: set[str] = set()

# locally opened goboscript projects
OPEN_PROJECTS: list[store.Project] = []

# path_to_project of active project
ACTIVE_PROJECT: str | None = None


## registry helpers


def _find(username: str) -> str | None:
    target = username.strip().casefold()
    for key in SESSIONS:
        if key.casefold() == target:
            return key
    return None


def _register(session: sa.Session, *, persist: bool) -> str:
    global ACTIVE

    username = session.username
    if not username:
        raise ToolError(
            "Logged in, but Scratch did not return a username for this session. "
            "The session id is probably malformed."
        )

    existing = _find(username)

    if existing is not None:
        SESSIONS[existing] = session
        username = existing
    else:
        SESSIONS[username] = session

    if persist: PERSISTED.add(username)

    if ACTIVE is None: ACTIVE = username

    return username


def _created_at(session: sa.Session) -> str | None:
    created = getattr(session, "time_created", None)
    if isinstance(created, datetime):
        return created.isoformat()
    return None


def _persist() -> None:
    now = datetime.now(timezone.utc).isoformat()

    records: list[store.Record] = [
        {
            "username": username,
            "session_id": session.id,
            "saved_at": now,
            "created_at": _created_at(session),
        }
        for username, session in SESSIONS.items()
        if username in PERSISTED and session.id
    ]

    active = ACTIVE if ACTIVE in PERSISTED else None

    store.write({
        "active": active,
        "sessions": records,
        "open_projects": OPEN_PROJECTS,
        "active_project": ACTIVE_PROJECT,
    })


def _restore() -> None:
    global ACTIVE, ACTIVE_PROJECT

    state = store.read()

    for record in state.get("sessions", []):
        try: session = sa.login_by_id(record["session_id"])
        except Exception: continue

        username = session.username or record["username"]
        SESSIONS[username] = session
        PERSISTED.add(username)

    stored_active = state.get("active")
    if stored_active:
        key = _find(stored_active)
        if key is not None: ACTIVE = key

    if ACTIVE is None and SESSIONS: ACTIVE = next(iter(SESSIONS))

    for entry in state.get("open_projects", []):
        if isinstance(entry, dict) and entry.get("path_to_project"):
            OPEN_PROJECTS.append(entry)

    ACTIVE_PROJECT = state.get("active_project")
    if _find_project(ACTIVE_PROJECT) is None:
        ACTIVE_PROJECT = OPEN_PROJECTS[0]["path_to_project"] if OPEN_PROJECTS else None


## project registry


def _find_project(path: str | None) -> store.Project | None:
    if not path: return None
    target = str(Path(path).expanduser().resolve())
    for entry in OPEN_PROJECTS:
        if entry.get("path_to_project") == target:
            return entry
    return None


def _register_project(entry: store.Project) -> store.Project:
    global ACTIVE_PROJECT

    existing = _find_project(entry["path_to_project"])
    if existing is not None:
        existing.update(entry)
        entry = existing
    else: OPEN_PROJECTS.append(entry)

    if ACTIVE_PROJECT is None: ACTIVE_PROJECT = entry["path_to_project"]

    return entry


def _set_active_project(path: str | None) -> None:
    global ACTIVE_PROJECT
    ACTIVE_PROJECT = path


def active_project() -> store.Project:
    if ACTIVE_PROJECT is None:
        raise ToolError(
            "No active project. Open one with `project_open`, download an "
            "existing Scratch project with `project_download`, or create one "
            "with `project_new`. `project_list` shows what is already open."
        )

    entry = _find_project(ACTIVE_PROJECT)
    if entry is None:
        raise ToolError(
            f"Active project '{ACTIVE_PROJECT}' is no longer registered. "
            "Re-open it with `project_open`."
        )
    return entry


def project_dir(entry: store.Project) -> Path:
    path = Path(entry["path_to_project"])
    if not path.is_dir():
        raise ToolError(
            f"Project directory '{path}' no longer exists on disk. "
            "Re-open or re-download it, or drop it with `project_close`."
        )
    return path


def _set_active(username: str | None) -> None:
    global ACTIVE
    ACTIVE = username


def _try_persist() -> str | None:
    try:
        _persist()
        return None
    except Exception as error: return f"{type(error).__name__}: {error}"


def active_ses() -> sa.Session:
    if ACTIVE is None:
        raise ToolError(
            "No active session. List available sessions with "
            "`social_list_sessions`, set one with `social_set_active_session`, "
            "or log in with `social_connect_session`."
        )

    session = SESSIONS.get(ACTIVE)
    if session is None:
        raise ToolError(
            f"Active session '{ACTIVE}' is no longer registered. "
            "Re-authenticate with `social_connect_session`."
        )
    return session


def maybe_ses() -> sa.Session | None: return SESSIONS.get(ACTIVE) if ACTIVE else None


def me() -> sa.User: return active_ses().connect_linked_user()


## object lookup


def get_user(username: str, *, authed: bool = False) -> sa.User:
    if authed:
        return active_ses().connect_user(username)

    session = maybe_ses()
    try:
        return session.connect_user(username) if session else sa.get_user(username)
    except Exception as error:
        raise ToolError(f"Could not fetch user '{username}': {type(error).__name__}: {error}") from error


def get_project(project_id: Union[int, str], *, authed: bool = False) -> sa.Project:
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        raise ToolError(f"'{project_id}' is not a valid project id.") from None

    if authed:
        return active_ses().connect_project(pid)

    session = maybe_ses()
    try:
        return session.connect_project(pid) if session else sa.get_project(pid)
    except Exception as error:
        raise ToolError(f"Could not fetch project {pid}: {type(error).__name__}: {error}") from error


## object -> dict conversion

class CommentInfo(TypedDict):
    id: Union[int, str]
    source: Optional[str]
    source_id: Union[int, str, None]
    parent_id: Union[int, str, None]
    is_top_level: bool
    author_name: Optional[str]
    author_id: Optional[int]
    written_by_scratchteam: Optional[bool]
    content: Optional[str]
    datetime_created: Optional[str]
    reply_count: Optional[int]
    commentee_id: Union[int, str, None]


class UserInfo(TypedDict):
    id: Optional[int]
    username: Optional[str]
    name: Optional[str]
    scratchteam: Optional[bool]
    join_date: Optional[str]
    country: Optional[str]
    about_me: Optional[str]
    wiwo: Optional[str]
    icon_url: Optional[str]
    profile_url: Optional[str]
    # None when there is no logged-in session to ask on behalf of.
    is_followed_by_me: Optional[bool]
    recent_comments: list[CommentInfo]
    comments_note: Optional[str]


class ProjectInfo(TypedDict):
    id: Optional[int]
    title: Optional[str]
    author_name: Optional[str]
    url: Optional[str]
    instructions: Optional[str]
    notes: Optional[str]
    created: Optional[str]
    last_modified: Optional[str]
    share_date: Optional[str]
    views: Optional[int]
    loves: Optional[int]
    favorites: Optional[int]
    remix_count: Optional[int]
    comments_allowed: Optional[bool]
    thumbnail_url: Optional[str]
    parent_title: Optional[str]
    remix_parent: Union[int, str, None]
    remix_root: Union[int, str, None]
    # None when there is no logged-in session to ask on behalf of.
    is_loved_by_me: Optional[bool]
    is_favorited_by_me: Optional[bool]
    recent_comments: list[CommentInfo]
    comments_note: Optional[str]


class CommentThread(TypedDict):
    comment: CommentInfo
    replies: list[CommentInfo]
    replies_included: bool
    replies_note: Optional[str]


class CommentPage(TypedDict):
    source: str
    source_id: Union[int, str]
    comments: list[CommentThread]
    returned: int

    # only one of these is works per source; see social_get_comments.
    limit: Optional[int]
    offset: Optional[int]
    page: Optional[int]
    has_more: Optional[bool]
    note: Optional[str]


_SOURCE_ALIASES = {"USER_PROFILE": "profile"}


def source_name(source) -> Optional[str]:
    if source is None: return None
    if isinstance(source, str): return source
    name = getattr(source, "name", None)
    if not isinstance(name, str): return str(source)
    return _SOURCE_ALIASES.get(name, name.lower())


def to_comment(comment: sa.Comment) -> CommentInfo:
    parent_id = getattr(comment, "parent_id", None)

    try: content = comment.text
    except Exception: content = getattr(comment, "content", None)

    return {
        "id": getattr(comment, "id", None),
        "source": source_name(getattr(comment, "source", None)),
        "source_id": getattr(comment, "source_id", None),
        "parent_id": parent_id,
        "is_top_level": parent_id is None,
        "author_name": getattr(comment, "author_name", None),
        "author_id": getattr(comment, "author_id", None),
        "written_by_scratchteam": getattr(comment, "written_by_scratchteam", None),
        "content": content,
        "datetime_created": getattr(comment, "datetime_created", None),
        "reply_count": getattr(comment, "reply_count", None),
        "commentee_id": getattr(comment, "commentee_id", None),
    }


def to_comments(comments) -> list[CommentInfo]: return [to_comment(c) for c in (comments or [])]


def to_thread(
    comment: sa.Comment,
    replies=None,
    *,
    included: bool,
    note: str | None = None,
) -> CommentThread:
    return {
        "comment": to_comment(comment),
        "replies": to_comments(replies),
        "replies_included": included,
        "replies_note": note,
    }


def to_user(user: sa.User, *, recent_comments: int = 0) -> UserInfo:
    username = getattr(user, "username", None)

    comments, note = _preview(
        lambda: fetch_user_comments(user, page=1, limit=recent_comments),
        recent_comments,
    )

    return {
        "id": getattr(user, "id", None),
        "username": username,
        "name": getattr(user, "name", None),
        "scratchteam": getattr(user, "scratchteam", None),
        "join_date": getattr(user, "join_date", None),
        "country": getattr(user, "country", None),
        "about_me": getattr(user, "about_me", None),
        "wiwo": getattr(user, "wiwo", None),
        "icon_url": getattr(user, "icon_url", None),
        "profile_url": f"https://scratch.mit.edu/users/{username}/" if username else None,
        "is_followed_by_me": followed_by_me(user),
        "recent_comments": comments,
        "comments_note": note,
    }


def to_project(
    project: sa.Project, *, recent_comments: int = 0, reactions: bool = True
) -> ProjectInfo:
    comments_allowed = getattr(project, "comments_allowed", None)

    if recent_comments and comments_allowed is False:
        comments, note = [], "Comments are turned off for this project."
    else:
        comments, note = _preview(
            lambda: fetch_project_comments(project, limit=recent_comments, offset=0),
            recent_comments,
        )

    return {
        "id": getattr(project, "id", None),
        "title": getattr(project, "title", None),
        "author_name": getattr(project, "author_name", None),
        "url": getattr(project, "url", None),
        "instructions": getattr(project, "instructions", None),
        "notes": getattr(project, "notes", None),
        "created": getattr(project, "created", None),
        "last_modified": getattr(project, "last_modified", None),
        "share_date": getattr(project, "share_date", None),
        "views": getattr(project, "views", None),
        "loves": getattr(project, "loves", None),
        "favorites": getattr(project, "favorites", None),
        "remix_count": getattr(project, "remix_count", None),
        "comments_allowed": comments_allowed,
        "thumbnail_url": getattr(project, "thumbnail_url", None),
        "parent_title": getattr(project, "parent_title", None),
        "remix_parent": getattr(project, "remix_parent", None),
        "remix_root": getattr(project, "remix_root", None),
        "is_loved_by_me": loved_by_me(project) if reactions else None,
        "is_favorited_by_me": favorited_by_me(project) if reactions else None,
        "recent_comments": comments,
        "comments_note": note,
    }


def _preview(fetch, count: int) -> tuple[list[CommentInfo], str | None]:
    if count <= 0:
        return [], None

    try:
        comments = fetch()
    except Exception as error:
        return [], f"Could not load comments: {type(error).__name__}: {error}"

    if not comments:
        return [], "No comments."

    return [to_comment(c) for c in comments[:count]], None


## comment fetching
#   project -> api.scratch.mit.edu, offset/limit, replies fetched separately
#   profile -> scraped HTML from site-api, page-based, replies already attached


def fetch_project_comments(project: sa.Project, *, limit: int, offset: int) -> list:
    if not getattr(project, "author_name", None):
        raise ToolError(
            f"Project {getattr(project, 'id', '?')} has no known author, so its "
            "comments cannot be fetched. It may be unshared."
        )
    return project.comments(limit=limit, offset=offset) or []


def fetch_user_comments(user: sa.User, *, page: int, limit: int) -> list:
    # scratchattach 3.x dropped the `limit` kwarg from User.comments (2.x
    # accepted it but silently ignored it anyway). `limit` is applied by the
    # caller, which is where the slicing always actually happened.
    return user.comments(page=page) or []


def fetch_project_replies(project: sa.Project, comment_id, *, limit: int, offset: int) -> list:
    return project.comment_replies(comment_id=comment_id, limit=limit, offset=offset) or []

## image inspection


def image_size(data: bytes) -> tuple[str, Optional[int], Optional[int]]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        import struct

        # IHDR always the first chunk, at fixed offset.
        width, height = struct.unpack(">II", data[16:24])
        return "png", width, height

    if data[:6] in (b"GIF87a", b"GIF89a"):
        import struct

        width, height = struct.unpack("<HH", data[6:10])
        return "gif", width, height

    if data[:2] == b"\xff\xd8":
        import struct

        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2 : i + 4])[0]
            # SOF0-SOF15
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return "jpeg", width, height
            i += 2 + length
        return "jpeg", None, None

    if b"<svg" in data[:1024]:
        return "svg", None, None

    return "unknown", None, None

## "what has the logged-in account done to this thing?"


def get_studio(studio_id: Union[int, str], *, authed: bool = False):
    """Fetch a studio, attaching the active session when there is one."""
    try:
        sid = int(studio_id)
    except (TypeError, ValueError):
        raise ToolError(f"'{studio_id}' is not a valid studio id.") from None

    session = active_ses() if authed else maybe_ses()
    if session is None:
        raise ToolError(
            "Reading a studio needs a logged-in session. Use "
            "`social_connect_session` first."
        )
    try:
        return session.connect_studio(sid)
    except Exception as error:
        raise ToolError(
            f"Could not fetch studio {sid}: {type(error).__name__}: {error}"
        ) from error


def followed_by_me(user: sa.User) -> Optional[bool]:
    """
    Whether the active session follows this user, or None if unknowable.

    Needs a session-attached User; `get_user` supplies one whenever a session
    exists. Never raises: this is a detail on an info call, not its purpose.
    """
    if maybe_ses() is None:
        return None
    try:
        return bool(user.is_followed_by_me())
    except Exception:
        return None


def _reaction(project: sa.Project, kind: str, key: str) -> Optional[bool]:
    """
    Read one of Scratch's per-user project reactions.

    scratchattach exposes love/unlove and favorite/unfavorite but no getter, so
    this reads the state endpoint directly. A GET only reports; only POST and
    DELETE change anything.
    """
    session = maybe_ses()
    if session is None:
        return None
    try:
        response = httpx.get(
            f"https://api.scratch.mit.edu/projects/{project.id}/{kind}/user/"
            f"{session.username}",
            headers=project._headers,
            cookies=project._cookies,
            timeout=20,
        )
        body = response.json()
    except Exception:
        return None
    value = body.get(key) if isinstance(body, dict) else None
    return bool(value) if isinstance(value, bool) else None


def loved_by_me(project: sa.Project) -> Optional[bool]:
    return _reaction(project, "loves", "userLove")


def favorited_by_me(project: sa.Project) -> Optional[bool]:
    return _reaction(project, "favorites", "userFavorite")


## studio comments
#
# Studios paginate like projects (offset/limit, replies fetched separately),
# unlike profiles.


def fetch_studio_comments(studio, *, limit: int, offset: int) -> list:
    return studio.comments(limit=limit, offset=offset) or []


def fetch_studio_replies(studio, comment_id, *, limit: int, offset: int) -> list:
    return studio.comment_replies(comment_id=comment_id, limit=limit, offset=offset) or []