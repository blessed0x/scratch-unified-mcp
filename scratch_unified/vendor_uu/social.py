import time
from typing import Literal, Optional

import httpx
import scratchattach as sa
from dotenv import dotenv_values
from fastmcp.exceptions import ToolError
from typing_extensions import TypedDict

from . import store, utils
from .server import mcp
from .utils import (
    BROWSERS,
    BrowserName,
    CommentInfo,
    CommentPage,
    CommentSource,
    ProjectInfo,
    UserInfo,
)

MAX_PER_REQUEST = 40

MAX_BIO = 200
MAX_WIWO = 255

MAX_COMMENT = 500


## session tools


@mcp.tool
def social_connect_session(
    path_to_env: Optional[str] = None,
    scratch_username: Optional[str] = None,
    scratch_password: Optional[str] = None,
    scratch_session_id: Optional[str] = None,
    browser_name: Optional[BrowserName] = None,
    remember: bool = True,
) -> str:
    """
    Login to the Scratch site using set login details.

    You may either provide a `path_to_env`, a `scratch_username` and `scratch_password`, or a `scratch_session_id`, or a `browser_name` (optional).

    If none is provided, it will fallback to logging in via the user's installed browser.

    Sessions are remembered across restarts by default, so this usually only needs to be called once per account. Check `social_list_sessions` first.

    Args:
        path_to_env: Path to a .env file containing SCRATCH_USERNAME and SCRATCH_PASSWORD environment variables, or a SCRATCH_SESSION_ID variable. If not provided, the below values must be instead.
        scratch_username: The username to authenticate with.
        scratch_password: The password to authenticate with.
        scratch_session_id: A session token from Scratch to use when authenticating. This value can be obtained from any logged-in browser by fetching the value of the cookie `scratchsessionsid`.
        browser_name: If none of the above are provided, it defaults to logging in via browser. Either pass a preferred browser to fetch the cookie from, or leave it empty to default to autodetect any installed browser and uses those credentials.
        remember: Save the session id to disk so the login survives a server restart. Only the session id is stored, never the password. Pass False for a login that lives only as long as this process.
    """

    if path_to_env:
        values = dotenv_values(path_to_env)
        scratch_username = values.get("SCRATCH_USERNAME")
        scratch_password = values.get("SCRATCH_PASSWORD")
        scratch_session_id = values.get("SCRATCH_SESSION_ID")

        if not (scratch_session_id or (scratch_username and scratch_password)):
            raise ToolError(
                f"'{path_to_env}' contains neither SCRATCH_SESSION_ID nor both of "
                "SCRATCH_USERNAME and SCRATCH_PASSWORD."
            )

    if scratch_username and scratch_password:
        session = sa.login(scratch_username, scratch_password)

    elif scratch_session_id:
        session = sa.login_by_id(scratch_session_id)

    else:
        browser = BROWSERS.get(browser_name or "autodetect", sa.Browser.ANY)
        session = sa.login_from_browser(browser)

    username = utils._register(session, persist=remember)

    if remember:
        failure = utils._try_persist()
        where = (
            f" (Could not save to disk: {failure})"
            if failure
            else f" Saved to {store.session_file()}."
        )
    else:
        where = " Not saved to disk; it will be lost when the server restarts."

    if utils.ACTIVE == username:
        note = "It is now the active session."
    else:
        note = (
            f"'{utils.ACTIVE}' is still the active session -- use "
            "`social_set_active_session` to switch."
        )

    return f"Logged in as '{username}'. {note}{where}"


@mcp.tool
def social_list_sessions() -> dict:
    """
    List the Scratch sessions this server currently holds.

    Use it to discover what is already available before asking the user for credentials.
    """
    return {
        "active": utils.ACTIVE,
        "store_path": str(store.session_file()),
        "sessions": [
            {
                "username": username,
                "is_active": username == utils.ACTIVE,
                "persisted": username in utils.PERSISTED,
                "created_at": utils._created_at(session),
            }
            for username, session in utils.SESSIONS.items()
        ],
    }


@mcp.tool
def social_set_active_session(username: str) -> str:
    """
    Choose which logged-in account subsequent authenticated tools act as.

    Args:
        username: Username of a session from `social_list_sessions`.
    """
    key = utils._find(username)
    if key is None:
        known = ", ".join(f"'{name}'" for name in utils.SESSIONS) or "none"
        raise ToolError(
            f"No session for '{username}'. Known sessions: {known}. "
            "Add one with `social_connect_session`."
        )

    utils._set_active(key)
    if key in utils.PERSISTED:
        utils._try_persist()

    return f"Successfully set active session to '{key}'."


@mcp.tool
def social_forget_session(username: str, logout: bool = False) -> str:
    """
    Drop a session from this server and from the on-disk store.

    Args:
        username: Username of the session to remove.
        logout: Also invalidate the session id server-side on Scratch. This breaks any other client using the same session id, including the user's browser if the login came from there.
    """
    key = utils._find(username)
    if key is None: raise ToolError(f"No session for '{username}'.")

    session = utils.SESSIONS.pop(key)
    utils.PERSISTED.discard(key)

    if utils.ACTIVE == key:
        utils._set_active(next(iter(utils.SESSIONS), None))

    notes = []
    if logout:
        try:
            session.logout()
            notes.append("Invalidated the session id on Scratch.")
        except Exception as error:
            notes.append(f"Server-side logout failed: {type(error).__name__}: {error}")

    failure = utils._try_persist()
    if failure:
        notes.append(f"Could not update the store: {failure}")

    notes.append(
        f"Active session is now {utils.ACTIVE!r}."
        if utils.ACTIVE
        else "No active session remains."
    )
    return f"Forgot session '{key}'. " + " ".join(notes)


@mcp.tool
def social_verify_session(username: Optional[str] = None) -> dict:
    """
    Check with Scratch whether a stored session id is still valid.

    Restored sessions are rebuilt offline from the session id, so an expired or
    revoked login looks fine until it is used. This performs a real request to
    confirm, and refreshes the account details on success.

    Args:
        username: Session to check. Defaults to the active session.
    """
    if username is None:
        session = utils.active_ses()
        key = utils.ACTIVE
    else:
        key = utils._find(username)
        if key is None:
            raise ToolError(f"No session for '{username}'.")
        session = utils.SESSIONS[key]

    try:
        result = session.update()
    except Exception as error:
        return {
            "username": key,
            "valid": False,
            "reason": f"{type(error).__name__}: {error}",
        }

    if result == "429":
        return {
            "username": key,
            "valid": None,
            "reason": "Rate limited by Scratch; try again shortly.",
        }

    if result is not True:
        return {
            "username": key,
            "valid": False,
            "reason": "Scratch rejected the session id. It has expired or been revoked; re-authenticate with `social_connect_session`.",
        }

    return {
        "username": session.username,
        "valid": True,
        "new_scratcher": session.new_scratcher,
        "banned": session.banned,
        "mute_status": session.mute_status,
    }


## profile tools


@mcp.tool
def social_set_bio(text: str) -> str:
    """
    Set the 'About me' section on the active session's profile.

    Args:
        text: The new bio. Max 200 characters; longer values are rejected here because Scratch would silently discard them.
    """
    _check_length("bio", text, MAX_BIO)
    utils.me().set_bio(text)
    return f"Set bio ({len(text)}/{MAX_BIO} characters)."


@mcp.tool
def social_set_whatimworkingon(text: str) -> str:
    """
    Set the "What I'm working on" section on the active session's profile.

    Args:
        text: The new status. Max 255 characters; longer values are rejected here because Scratch would silently discard them.
    """
    _check_length("wiwo", text, MAX_WIWO)
    utils.me().set_wiwo(text)
    return f"Set wiwo ({len(text)}/{MAX_WIWO} characters)."


def _check_length(field: str, text: str, maximum: int) -> None:
    if len(text) > maximum:
        raise ToolError(
            f"{field} is {len(text)} characters; Scratch's limit is {maximum}. "
            f"Shorten it by {len(text) - maximum} characters."
        )


@mcp.tool
def social_get_user_info(username: str, recent_comments: int = 5) -> UserInfo:
    """
    Fetch a Scratch user's profile.

    Args:
        username: The account to look up.
        recent_comments: How many of the newest profile comments to include as a preview. Set 0 to skip the extra request. Use `social_get_comments` to page through them all.
    """
    user = utils.get_user(username)
    return utils.to_user(user, recent_comments=max(0, recent_comments))


@mcp.tool
def social_get_project_info(id: str, recent_comments: int = 5) -> ProjectInfo:
    """
    Fetch a Scratch project's metadata.

    Args:
        id: The numeric project id.
        recent_comments: How many of the newest project comments to include as a preview. Set 0 to skip the extra request. Use `social_get_comments` to page through them all.
    """
    project = utils.get_project(id)
    return utils.to_project(project, recent_comments=max(0, recent_comments))


def _offset_source(source: str, source_id: str):
    if source == "studio":
        studio = utils.get_studio(source_id)
        return studio, utils.fetch_studio_comments, utils.fetch_studio_replies
    project = utils.get_project(source_id)
    return project, utils.fetch_project_comments, utils.fetch_project_replies


## comment tools


@mcp.tool
def social_get_comments(
    source: CommentSource,
    source_id: str,
    limit: int = 20,
    offset: int = 0,
    page: int = 1,
    include_replies: bool = False,
) -> CommentPage:
    """
    Page through the comments on a project, a studio, or a user's profile.

    The sources paginate differently because Scratch exposes them differently,
    so read the argument notes:

    - source="project" / "studio": uses `limit` + `offset`. Replies are a
      separate request, so `include_replies` costs one extra request per
      comment.
    - source="profile": uses `page` (30 top-level comments per page); `offset`
      is ignored. Replies always come back, free of charge, so `include_replies`
      is irrelevant here.

    Only top-level comments are listed. Scratch has a single level of nesting:
    a reply to a reply is stored as a reply to the top-level comment.

    Both sources page over a live feed, so on a busy project or profile a
    comment can shift between pages while you read them.

    Args:
        source: "project", "studio", or "profile" for a user's profile.
        source_id: The numeric project or studio id, or the username, matching `source`.
        limit: Max top-level comments to return (1-40).
        offset: How many comments to skip. project and studio only.
        page: Which page of profile comments to read, starting at 1. profile only.
        include_replies: Fetch replies for each comment. project and studio only; costs one request per comment.
    """
    limit = max(1, min(limit, MAX_PER_REQUEST))

    if source in ("project", "studio"):
        target, fetch_comments, fetch_replies = _offset_source(source, source_id)

        if getattr(target, "comments_allowed", None) is False:
            return {
                "source": source,
                "source_id": target.id,
                "comments": [],
                "returned": 0,
                "limit": limit,
                "offset": offset,
                "page": None,
                "has_more": False,
                "note": f"Comments are turned off for this {source}.",
            }

        if offset < 0: raise ToolError("offset must be >= 0.")

        comments = fetch_comments(target, limit=limit, offset=offset)

        threads = []
        for comment in comments:
            if not include_replies:
                threads.append(utils.to_thread(comment, included=False))
                continue

            if not getattr(comment, "reply_count", 0):
                threads.append(utils.to_thread(comment, [], included=True))
                continue

            try:
                replies = fetch_replies(target, comment.id, limit=MAX_PER_REQUEST, offset=0)
                threads.append(utils.to_thread(comment, replies, included=True))
            except Exception as error:
                threads.append(utils.to_thread(comment, included=False, note=f"Could not load replies: {type(error).__name__}: {error}"))

        return {
            "source": source,
            "source_id": target.id,
            "comments": threads,
            "returned": len(threads),
            "limit": limit,
            "offset": offset,
            "page": None,
            "has_more": len(comments) >= limit,
            "note": None
            if include_replies
            else "Replies omitted. Pass include_replies=true, or use `social_get_comment_replies` for one comment.",
        }

    if page < 1: raise ToolError("page must be >= 1.")

    user = utils.get_user(source_id)
    comments = utils.fetch_user_comments(user, page=page, limit=limit)

    truncated = comments[:limit]

    threads = [utils.to_thread(comment, comment.cached_replies, included=True) for comment in truncated]

    return {
        "source": source,
        "source_id": getattr(user, "username", source_id),
        "comments": threads,
        "returned": len(threads),
        "limit": limit,
        "offset": None,
        "page": page,
        "has_more": True if len(comments) > len(truncated) else (None if comments else False),
        "note": "Profile comments are paginated by `page`; `offset` is ignored. Replies are always included.",
    }


@mcp.tool
def social_get_comment_replies(
    source: CommentSource,
    source_id: str,
    comment_id: str,
    limit: int = 40,
    offset: int = 0,
) -> CommentPage:
    """
    Fetch the replies to one top-level comment.

    For source="profile" this is slow: Scratch has no endpoint for a single
    profile comment, so scratchattach pages through the profile until it finds
    the id. Prefer `social_get_comments`, which returns profile replies inline.

    Args:
        source: "project", "studio", or "profile".
        source_id: The numeric project or studio id, or the username, matching `source`.
        comment_id: Id of the top-level comment whose replies you want.
        limit: Max replies to return (1-40). project and studio only.
        offset: How many replies to skip. project and studio only.
    """
    limit = max(1, min(limit, MAX_PER_REQUEST))

    if source in ("project", "studio"):
        target, _fetch_comments, fetch_replies = _offset_source(source, source_id)
        try:
            replies = fetch_replies(target, comment_id, limit=limit, offset=offset)
        except Exception as error:
            raise ToolError(
                f"Could not load replies to comment {comment_id}: {type(error).__name__}: {error}"
            ) from error

        return {
            "source": source,
            "source_id": target.id,
            "comments": [utils.to_thread(r, included=False) for r in replies],
            "returned": len(replies),
            "limit": limit,
            "offset": offset,
            "page": None,
            "has_more": len(replies) >= limit,
            "note": f"Replies to comment {comment_id}.",
        }

    user = utils.get_user(source_id)
    try: comment = user.comment_by_id(comment_id)
    except Exception as error:
        raise ToolError(
            f"Could not find comment {comment_id} on '{source_id}': "
            f"{type(error).__name__}: {error}"
        ) from error

    replies = list(comment.cached_replies or [])[offset : offset + limit]

    return {
        "source": source,
        "source_id": getattr(user, "username", source_id),
        "comments": [utils.to_thread(r, included=False) for r in replies],
        "returned": len(replies),
        "limit": limit,
        "offset": offset,
        "page": None,
        "has_more": False,
        "note": f"Replies to comment {comment_id}.",
    }


@mcp.tool
def social_post_comment(
    source: CommentSource,
    source_id: str,
    content: str,
    parent_id: Optional[str] = None,
    commentee_id: Optional[str] = None,
) -> CommentInfo:
    """
    Post a comment on a project, a studio, or a user's profile, as the active session.

    Requires a logged-in session. To reply to an existing comment, prefer
    `social_reply_to_comment`.

    Scratch rate-limits commenting and rejects content it considers spam or
    disallowed, which surfaces as a CommentPostFailure.

    Args:
        source: "project", "studio", or "profile" to comment on a user's profile.
        source_id: The numeric project or studio id, or the username, matching `source`.
        content: The comment text. Max 500 characters.
        parent_id: Id of a top-level comment to reply to. Leave unset for a new top-level comment.
        commentee_id: Numeric user id to @-mention and notify. Leave unset for none.
    """
    if not content.strip():
        raise ToolError("Comment content is empty.")

    _check_length("comment", content, MAX_COMMENT)

    target = _comment_target(source, source_id)

    try:
        comment = target.post_comment(
            content,
            parent_id=parent_id or "",
            commentee_id=commentee_id or "",
        )
    except Exception as error:
        raise ToolError(f"Could not post the comment: {type(error).__name__}: {error}") from error

    if comment is None:
        raise ToolError(
            "Scratch accepted the request but returned no comment. It may have "
            "been filtered; check with `social_get_comments`."
        )

    return utils.to_comment(comment)


@mcp.tool
def social_reply_to_comment(
    source: CommentSource,
    source_id: str,
    parent_id: str,
    content: str,
    commentee_id: Optional[str] = None,
) -> CommentInfo:
    """
    Reply to an existing comment on a project, studio or profile, as the active session.

    `parent_id` must be a TOP-LEVEL comment id. Scratch supports only one level
    of nesting, so replying to a reply must target that reply's top-level
    parent; `social_get_comments` reports `is_top_level` and `parent_id` for
    every comment so you can pick the right id.

    Args:
        source: "project", "studio", or "profile".
        source_id: The numeric project or studio id, or the username, matching `source`.
        parent_id: Id of the top-level comment being replied to.
        content: The reply text. Max 500 characters.
        commentee_id: Numeric user id to @-mention and notify, normally the author of the comment you are replying to. Leave unset for none.
    """
    if not content.strip(): raise ToolError("Reply content is empty.")
    if not parent_id: raise ToolError("parent_id is required; use `social_post_comment` for a new thread.")

    _check_length("reply", content, MAX_COMMENT)

    target = _comment_target(source, source_id)

    try:
        comment = target.reply_comment(content, parent_id=parent_id, commentee_id=commentee_id or "")
    except Exception as error:
        raise ToolError(f"Could not post the reply: {type(error).__name__}: {error}") from error

    if comment is None:
        raise ToolError(
            "Scratch accepted the request but returned no comment. It may have "
            "been filtered; check with `social_get_comments`."
        )

    return utils.to_comment(comment)


def _comment_target(source: str, source_id: str):
    """The authenticated object to post a comment through."""
    utils.active_ses()

    if source == "project": return utils.get_project(source_id, authed=True)
    if source == "studio": return utils.get_studio(source_id, authed=True)
    return utils.get_user(source_id, authed=True)


MAX_PFP_SIDE = 500
PFP_FORMATS = {"png", "jpeg", "gif"}


@mcp.tool
def social_set_pfp(file: str) -> dict:
    """
    Set the profile picture of the active session's account.

    Scratch caps avatars at 500x500 pixels. An oversized image is rejected with
    HTTP 200 and an error in the body, so this checks the size before uploading
    and reads the response rather than assuming success.

    SVG is not accepted by Scratch; rasterise it first (for example `inkscape logo.svg -w 500 -h 500 -o logo.png`).

    Args:
        file: Path to a .png, .jpg or .gif no larger than 500x500.
    """
    from pathlib import Path

    path = Path(file).expanduser().resolve()
    if not path.is_file():
        raise ToolError(f"'{path}' does not exist.")

    data = path.read_bytes()
    kind, width, height = utils.image_size(data)

    if kind == "svg":
        raise ToolError(
            f"'{path.name}' is an SVG, which Scratch will not accept as an "
            f"avatar. Rasterise it first, e.g. "
            f"`inkscape {path.name} -w {MAX_PFP_SIDE} -h {MAX_PFP_SIDE} -o out.png`."
        )
    if kind not in PFP_FORMATS: raise ToolError(f"'{path.name}' is not a PNG, JPEG or GIF (detected: {kind}).")
    if width and height and (width > MAX_PFP_SIDE or height > MAX_PFP_SIDE):
        raise ToolError(
            f"'{path.name}' is {width}x{height}; Scratch's avatar limit is "
            f"{MAX_PFP_SIDE}x{MAX_PFP_SIDE} and it would reject this with "
            f"'thumbnail-too-large' while still returning HTTP 200. "
            f"Resize it, e.g. `magick {path.name} -resize {MAX_PFP_SIDE}x{MAX_PFP_SIDE} out.png`."
        )

    user = utils.me()
    try:
        response = httpx.post(
            f"https://scratch.mit.edu/site-api/users/all/{user.username}/",
            headers=user._headers,
            cookies=user._cookies,
            files={"file": data},
            timeout=60,
        )
    except Exception as error: raise ToolError(f"Upload failed: {type(error).__name__}: {error}") from error

    body: dict = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict): body = parsed
    except ValueError: ...

    errors = body.get("errors")
    if errors:
        raise ToolError(
            f"Scratch rejected the image: {', '.join(str(e) for e in errors)}. "
            f"(HTTP {response.status_code}; the image was {width}x{height}, "
            f"{len(data) // 1024} KB.)"
        )

    if response.status_code != 200:
        raise ToolError(
            f"Upload failed with HTTP {response.status_code}: {response.text[:200]}"
        )

    if not body.get("thumbnail_url"):
        raise ToolError(
            f"Scratch accepted the request but returned no thumbnail_url, so "
            f"the avatar may not have changed. Response: {response.text[:200]}"
        )

    return {
        "username": user.username,
        "format": kind,
        "width": width,
        "height": height,
        "kilobytes": round(len(data) / 1024, 1),
        "thumbnail_url": body["thumbnail_url"],
    }

## inbox, follows and reactions

FollowAction = Literal["toggle", "follow", "unfollow", "check"]
ReactAction = Literal[
    "like", "favourite", "both", "unlike", "unfavourite", "removeboth", "check"
]


class InboxResult(TypedDict):
    unread_count: Optional[int]
    returned: int
    limit: int
    offset: int
    messages: list[dict]
    scratch_team_messages: list[dict]
    invitation: Optional[dict]
    unread_elsewhere: bool
    unavailable: list[str]
    note: Optional[str]


@mcp.tool
def social_check_inbox(limit: int = 20, offset: int = 0) -> InboxResult:
    """
    Check the active account's message inbox.

    Scratch splits the inbox across three endpoints with independent unread
    state, and this reports all of them:

    - `unread_count` + `messages`: ordinary activity (comments, follows, loves).
      The count is genuinely unread, but the list is simply the newest messages
      whether read or not, so the two do not necessarily correspond.
    - `scratch_team_messages`: alerts from the Scratch Team.
    - `invitation`: a pending "become a Scratcher" invite, carrying its own
      `unread` flag.

    `unread_elsewhere` is true when something is unread outside the activity
    feed, so an `unread_count` of 0 does not by itself mean an empty inbox.

    Args:
        limit: How many messages to return (1-40).
        offset: How many of the newest messages to skip.
    """
    session = utils.active_ses()
    limit = max(1, min(limit, MAX_PER_REQUEST))
    if offset < 0: raise ToolError("offset must be >= 0.")

    unavailable: list[str] = []

    try: unread = int(session.message_count())
    except Exception:
        unread = None
        unavailable.append("unread count")

    try: activities = session.messages(limit=limit, offset=offset) or []
    except Exception:
        activities = []
        unavailable.append("activity messages")
    
    messages: list[dict] = []
    for activity in activities:
        raw = getattr(activity, "raw", None)
        messages.append(raw if isinstance(raw, dict) else {
            "id": getattr(activity, "id", None),
            "type": getattr(activity, "type", None),
            "actor_username": getattr(activity, "actor_username", None),
        })

    try: team = session.admin_messages(limit=limit, offset=0) or []
    except Exception: team = []

    invitation = None
    try:
        found = session.become_scratcher_invite()
        if isinstance(found, dict) and found: invitation = found
    except Exception: pass

    invite_unread = bool((invitation or {}).get("unread"))
    unread_elsewhere = invite_unread or bool(team)

    notes: list[str] = []
    if unavailable:
        notes.append(
            f"Scratch refused these: {', '.join(unavailable)}. That is normal for "
            f"a blocked account, which can still read its Scratch Team messages."
        )
    if unread is not None and unread > len(messages) + offset:
        notes.append(
            f"{unread} unread but only {len(messages)} returned; raise `limit` "
            f"or page with `offset`."
        )
    elif unread == 0 and not unread_elsewhere and not unavailable: notes.append("Nothing unread. The messages listed are the newest, already-read ones.")
    if invite_unread:
        notes.append(
            "There is an unread invitation to become a Scratcher, tracked "
            "separately from `unread_count`."
        )
    if team: notes.append(f"{len(team)} message(s) from the Scratch Team.")
    if unread == 0 and unread_elsewhere:
        notes.append(
            "The activity counter is 0, so any badge on the site is coming from "
            "the above rather than from comments or follows."
        )

    return {
        "unread_count": unread,
        "returned": len(messages),
        "limit": limit,
        "offset": offset,
        "messages": messages,
        "scratch_team_messages": team,
        "invitation": invitation,
        "unread_elsewhere": unread_elsewhere,
        "unavailable": unavailable,
        "note": " ".join(notes) or None,
    }


@mcp.tool
def social_follow_user(username: str, action: FollowAction = "check") -> dict:
    """
    Follow, unfollow, or check whether the active account follows a user.

    Args:
        username: The account to act on.
        action: "check" to only report (the default, since it changes nothing), "follow", "unfollow", or "toggle" to invert the current state.
    """
    session = utils.active_ses()
    if username.strip().casefold() == (session.username or "").casefold(): raise ToolError("You cannot follow your own account.") # i mean you probably can but

    user = utils.get_user(username, authed=True)

    try: before = bool(user.is_followed_by_me())
    except Exception as error:
        raise ToolError(
            f"Could not read follow state for '{username}': "
            f"{type(error).__name__}: {error}"
        ) from error

    if action == "check":
        return {
            "username": getattr(user, "username", username),
            "action": action,
            "was_following": before,
            "is_following": before,
            "changed": False,
        }

    want = {"follow": True, "unfollow": False, "toggle": not before}[action]

    if want != before:
        try: user.follow() if want else user.unfollow()
        except Exception as error:
            raise ToolError(
                f"Could not {'follow' if want else 'unfollow'} '{username}': "
                f"{type(error).__name__}: {error}"
            ) from error

    try: after = bool(user.is_followed_by_me())
    except Exception: after = want

    return {
        "username": getattr(user, "username", username),
        "action": action,
        "was_following": before,
        "is_following": after,
        "changed": after != before,
    }


@mcp.tool
def social_like_project(id: str, action: ReactAction = "check") -> dict:
    """
    Love and/or favourite a project, remove either, or just check.

    "like" is Scratch's love (the heart); "favourite" is the star. They are
    independent, so `both` and `removeboth` operate on each.

    Args:
        id: The numeric project id.
        action: "check" to only report (the default), "like", "favourite", "both", "unlike", "unfavourite", or "removeboth".
    """
    utils.active_ses()
    project = utils.get_project(id, authed=True)

    before_love = utils.loved_by_me(project)
    before_fav = utils.favorited_by_me(project)

    after_love, after_fav = before_love, before_fav

    if action != "check":
        steps: list[tuple[str, bool]] = {
            "like": [("love", True)],
            "favourite": [("favorite", True)],
            "both": [("love", True), ("favorite", True)],
            "unlike": [("love", False)],
            "unfavourite": [("favorite", False)],
            "removeboth": [("love", False), ("favorite", False)],
        }[action]

        for what, on in steps:
            method = what if on else ("unlove" if what == "love" else "unfavorite")
            try:
                getattr(project, method)()
            except Exception as error:
                raise ToolError(
                    f"Could not {method} project {id}: "
                    f"{type(error).__name__}: {error}"
                ) from error
            if what == "love": after_love = on
            else: after_fav = on

    return {
        "project_id": project.id,
        "title": getattr(project, "title", None),
        "url": f"https://scratch.mit.edu/projects/{project.id}/",
        "action": action,
        "loved": after_love,
        "favorited": after_fav,
        "was_loved": before_love,
        "was_favorited": before_fav,
        "changed": (after_love != before_love) or (after_fav != before_fav),
    }


@mcp.tool
def social_add_project_to_studio(project_id: str, studio_id: str) -> str:
    """
    Add a project to a studio as the active account.

    Scratch only allows this if the account may add to that studio: it must be
    the owner, a curator, or the studio must allow anyone to add.

    Args:
        project_id: The numeric project id to add.
        studio_id: The numeric studio id to add it to.
    """
    utils.active_ses()
    try: pid = int(project_id)
    except (TypeError, ValueError): raise ToolError(f"'{project_id}' is not a valid project id.") from None

    studio = utils.get_studio(studio_id, authed=True)

    try: studio.add_project(pid)
    except Exception as error:
        raise ToolError(
            f"Could not add project {pid} to studio {studio_id}: "
            f"{type(error).__name__}: {error}. The active session must own the "
            f"studio, curate it, or the studio must let anyone add projects."
        ) from error

    return (
        f"Added project {pid} to studio {studio_id}. "
        f"https://scratch.mit.edu/studios/{studio_id}/"
    )

## search

SearchSort = Literal["trending", "popular", "recent"]

MAX_SEARCH = 40


class ProjectHit(TypedDict):
    id: Optional[int]
    title: Optional[str]
    author_name: Optional[str]
    url: Optional[str]
    thumbnail_url: Optional[str]
    views: Optional[int]
    loves: Optional[int]
    favorites: Optional[int]
    remix_count: Optional[int]
    created: Optional[str]
    last_modified: Optional[str]
    instructions: Optional[str]


class ProjectSearch(TypedDict):
    query: Optional[str]
    sort: str
    browsing: bool
    language: str
    limit: int
    offset: int
    returned: int
    has_more: Optional[bool]
    results: list[ProjectHit]
    note: Optional[str]


def _to_hit(project) -> ProjectHit:
    pid = getattr(project, "id", None)
    text = getattr(project, "instructions", None) or ""
    return {
        "id": pid,
        "title": getattr(project, "title", None),
        "author_name": getattr(project, "author_name", None),
        "url": f"https://scratch.mit.edu/projects/{pid}/" if pid else None,
        "thumbnail_url": getattr(project, "thumbnail_url", None),
        "views": getattr(project, "views", None),
        "loves": getattr(project, "loves", None),
        "favorites": getattr(project, "favorites", None),
        "remix_count": getattr(project, "remix_count", None),
        "created": getattr(project, "created", None),
        "last_modified": getattr(project, "last_modified", None),
        "instructions": (text[:280] + "...") if len(text) > 280 else (text or None),
    }


@mcp.tool
def social_search_projects(
    query: Optional[str] = None,
    sort: SearchSort = "trending",
    limit: int = 20,
    offset: int = 0,
    language: str = "en",
) -> ProjectSearch:
    """
    Search Scratch's shared projects, or browse them when no query is given.

    Results are a lean summary: id, title, author, url, thumbnail and stats.
    Follow up with `social_get_project_info` for the full record of one project,
    or `project_download` to pull one apart and edit it.

    Note: as of 27/07/26, the API is broken right now, so if it doesn't work it's an upstream issue -- not our one.
    You can, if you wish, search the web for status updates as to Scratch search functionality.

    Args:
        query: Words to search for. Leave unset to browse instead of search.
        sort: "trending" (default), "popular", or "recent".
        limit: How many results to return (1-40).
        offset: How many results to skip, for paging.
        language: Two-letter language code Scratch should rank for.
    """
    limit = max(1, min(limit, MAX_SEARCH))
    if offset < 0:
        raise ToolError("offset must be >= 0.")

    text = (query or "").strip()
    browsing = not text
    session = utils.maybe_ses()

    def fetch():
        source = session if session is not None else sa
        if browsing: return source.explore_projects(query="*", mode=sort, language=language, limit=limit, offset=offset)
        return source.search_projects(query=text, mode=sort, language=language, limit=limit, offset=offset)

    results = None
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            results = fetch()
            break
        except Exception as error:
            last_error = error
            if attempt < 2: time.sleep(2 + 3 * attempt)

    if results is None and last_error is not None:
        name = type(last_error).__name__
        detail = str(last_error)
        outage = any(
            marker in detail or marker in name
            for marker in ("429", "503", "Timeout", "timed out", "FetchError")
        )
        if outage:
            raise ToolError(
                f"Scratch's search service is not responding ({name}). This is a "
                f"Scratch-side problem, not a problem with the query: search "
                f"regularly returns HTTP 429 or a 503 from its cache layer and "
                f"is sometimes down for everyone. Check whether the search bar "
                f"on scratch.mit.edu works; if it does not, only waiting helps. "
                f"Other tools such as `social_get_project_info` are unaffected."
            ) from last_error
        raise ToolError(f"Search failed after 3 attempts: {name}: {detail}") from last_error

    results = results or []
    hits = [_to_hit(p) for p in results]

    return {
        "query": text or None,
        "sort": sort,
        "browsing": browsing,
        "language": language,
        "limit": limit,
        "offset": offset,
        "returned": len(hits),
        "has_more": len(hits) >= limit if hits else False,
        "results": hits,
        "note": ("No query given, so this is a browse of Scratch's explore feed rather than a search.") if browsing else None,
    }


class ScratcherResult(TypedDict):
    username: Optional[str]
    rank: str
    invited: bool
    was_scratcher: bool
    is_scratcher: bool
    promoted: bool
    note: str


def _permissions(session) -> dict:
    response = httpx.post(
        "https://scratch.mit.edu/session",
        headers=session._headers,
        cookies=session._cookies,
        timeout=25,
    )
    body = response.json()
    perms = body.get("permissions") if isinstance(body, dict) else None
    if not isinstance(perms, dict):
        raise ToolError(
            f"Could not read the account's permissions (HTTP {response.status_code})."
        )
    return perms


@mcp.tool
def social_become_scratcher(confirm: bool = False) -> ScratcherResult:
    """
    Accept a "become a Scratcher" invitation for the active account.

    Scratch promotes a New Scratcher only after the Scratch Team invites them.
    On the website the invitation is accepted by reading through the Community
    Guidelines and clicking to agree, so accepting here means the account holder
    accepts those guidelines. For that reason nothing happens unless `confirm`
    is set: called without it, this only reports eligibility.

    The guidelines are at https://scratch.mit.edu/community_guidelines and ask
    everyone on Scratch, in summary, to: be respectful, remembering the audience
    is broad and includes children; be constructive when commenting; share
    freely and give credit when remixing; keep personal information private; be
    honest rather than impersonating others or spreading rumours; and report
    anything inappropriate rather than escalating it.

    Promotion cannot be undone.

    Args:
        confirm: Set true to actually accept. Left false, this reports the account's rank and whether an invitation is pending, and changes nothing.
    """
    session = utils.active_ses()
    perms = _permissions(session)

    was_scratcher = bool(perms.get("scratcher"))
    invited = bool(perms.get("invited_scratcher"))
    username = session.username

    if was_scratcher:
        return {
            "username": username, "rank": "scratcher", "invited": invited,
            "was_scratcher": True, "is_scratcher": True, "promoted": False,
            "note": f"'{username}' is already a Scratcher. Nothing to do.",
        }

    if not invited:
        raise ToolError(
            f"'{username}' has no pending invitation, so it cannot be promoted. "
            f"Only the Scratch Team issues these, and it cannot be requested or "
            f"forced. `social_check_inbox` reports one as soon as it arrives."
        )

    if not confirm:
        return {
            "username": username, "rank": "new scratcher", "invited": True,
            "was_scratcher": False, "is_scratcher": False, "promoted": False,
            "note": (
                f"'{username}' has a pending invitation and can be promoted. "
                f"Accepting means the account holder agrees to Scratch's "
                f"Community Guidelines (https://scratch.mit.edu/community_guidelines), "
                f"which is what the website's click-through represents, and it "
                f"cannot be undone. Call again with confirm=true to accept."
            ),
        }

    response = httpx.get(
        f"https://scratch.mit.edu/users/{username}/promote-to-scratcher/",
        headers=session._headers,
        cookies=session._cookies,
        timeout=30,
    )
    if response.status_code >= 400:
        raise ToolError(
            f"Scratch refused the promotion (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )

    now_scratcher = bool(_permissions(session).get("scratcher"))
    return {
        "username": username,
        "rank": "scratcher" if now_scratcher else "new scratcher",
        "invited": invited,
        "was_scratcher": False,
        "is_scratcher": now_scratcher,
        "promoted": now_scratcher,
        "note": (
            f"'{username}' is now a Scratcher."
            if now_scratcher else
            f"Scratch accepted the request (HTTP {response.status_code}) but the "
            f"account still reads as a New Scratcher. Check "
            f"https://scratch.mit.edu/users/{username}/ before retrying."
        ),
    }
