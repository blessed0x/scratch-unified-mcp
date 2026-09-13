import os, json
from pathlib import Path
from typing import TypedDict

class Record(TypedDict, total=False):
    username: str
    session_id: str
    saved_at: str
    created_at: str | None

class Project(TypedDict, total=False):
    path_to_project: str # before editing a project must be downloaded locally; agent will pass the project path to download to (usually in his configured workspace) where the .sb3 will be extracted; if the project intends to be published online and not just a local offline one, then the project ID will be stored as well and when the agent calls a tool such as `project_save_to_cloud` it will save the active project to the cloud by publishing it using scratchattach
    published_project_id: str

    is_scratch_compatible: bool # SOME turbowarp extensions are not compatible with Scratch; use TurboWarp to play these instead. whenever one of these turbowarp-only extensions is added, the project_id must be removed and errors thrown when the agent attempts to publish to scratch, as scratch will not except it; optionally perhaps leave the project ID in the config and tell the agent which extensions to remove in order to make it uploadable if he wants


class State(TypedDict):
    active: str | None = None
    sessions: list[Record] = []

    open_projects: list[Project] = []
    active_project: str | None = None


def data_dir() -> Path:
    override = os.getenv("SCRATCH_MCP_DATA_DIR")
    if override:
        return Path(override).expanduser()

    return Path.home() / ".local" / "share" / "scratch-mcp"


def session_file() -> Path:
    override = os.getenv("SCRATCH_MCP_SESSION_FILE")
    if override:
        return Path(override).expanduser()
    
    return data_dir() / "sessions.json"


def read() -> State:
    try: return json.loads(session_file().read_text())
    except: return State()


def write(state: State) -> Path:
    path = session_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(json.dumps(state, indent=2, sort_keys=True))

    return path


def clear() -> None:
    session_file().unlink(missing_ok=True)