"""Typed per-tool Python functions for the Node sidecar proxy.

FastMCP rejects **kwargs tools, so each sb3_* tool gets an explicit function
with concrete parameters. Parameters mirror upstream-scratch4js
packages/scratch-mcp/src/index.js inputSchema shapes; everything optional
except where upstream requires it.
"""
from .node_bridge import SIDECAR

__all__ = ["SB3_TOOL_DEFS"]


def sb3_open_project(path: str) -> str:
    """Load an .sb3 file into the Node editor. (proxied)"""
    return SIDECAR.call_tool("open_project", {"path": path})


def sb3_save_project(path: str = "", compressionLevel: int = 0) -> str:
    """Write the open project back to .sb3, live-reloading TurboWarp. (proxied)"""
    args = {}
    if path:
        args["path"] = path
    if compressionLevel:
        args["compressionLevel"] = compressionLevel
    return SIDECAR.call_tool("save_project", args)


def sb3_project_info() -> str:
    """Targets, extensions, monitors, meta of open project. (proxied)"""
    return SIDECAR.call_tool("project_info", {})


def sb3_scratch_login(username: str = "", password: str = "") -> str:
    """Log in to scratch.mit.edu for the Node session. (proxied)"""
    args = {}
    if username:
        args["username"] = username
    if password:
        args["password"] = password
    return SIDECAR.call_tool("scratch_login", args)


def sb3_open_scratch_project(projectId: str) -> str:
    """Download a scratch.mit.edu project by id for editing. (proxied)"""
    return SIDECAR.call_tool("open_scratch_project", {"projectId": projectId})


def sb3_push_to_scratch(projectId: str = "", confirm: bool = False) -> str:
    """Save the open project back to scratch.mit.edu (confirm-gated). (proxied)"""
    args = {"confirm": confirm}
    if projectId:
        args["projectId"] = projectId
    return SIDECAR.call_tool("push_to_scratch", args)


def sb3_share_project(projectId: str = "", confirm: bool = False) -> str:
    """Publish the project so it is public (confirm-gated). (proxied)"""
    args = {"confirm": confirm}
    if projectId:
        args["projectId"] = projectId
    return SIDECAR.call_tool("share_project", args)


def sb3_list_sprites() -> str:
    """Every sprite with position/size/media. (proxied)"""
    return SIDECAR.call_tool("list_sprites", {})


def sb3_get_target(name: str) -> str:
    """Full details for a sprite or Stage. (proxied)"""
    return SIDECAR.call_tool("get_target", {"name": name})


def sb3_get_target_json(name: str, pointer: str = "") -> str:
    """Raw project.json entry for a target, or subtree at a JSON Pointer. (proxied)"""
    args = {"name": name}
    if pointer:
        args["pointer"] = pointer
    return SIDECAR.call_tool("get_target_json", args)


def sb3_list_blocks(category: str = "") -> str:
    """Catalog of standard opcodes from scratch-vm, optionally by category. (proxied)"""
    return SIDECAR.call_tool("list_blocks", {"category": category} if category else {})


def sb3_get_block_schema(opcode: str, target: str = "") -> str:
    """Full schema for one opcode incl. shadow encodings. (proxied)"""
    args = {"opcode": opcode}
    if target:
        args["target"] = target
    return SIDECAR.call_tool("get_block_schema", args)


def sb3_enable_extension(id: str, url: str = "") -> str:
    """Register an extension so its blocks load. (proxied)"""
    args = {"id": id}
    if url:
        args["url"] = url
    return SIDECAR.call_tool("enable_extension", args)


def sb3_patch_target(name: str, patch: str) -> str:
    """Apply RFC 6902 JSON Patch (as JSON string) to a target. (proxied)"""
    import json as _json
    try:
        patch_arg = _json.loads(patch)
    except Exception:
        patch_arg = patch
    return SIDECAR.call_tool("patch_target", {"name": name, "patch": patch_arg})


def sb3_set_sprite(name: str, props: str = "{}") -> str:
    """Set sprite props; props is a JSON object string (x, y, size, ...). (proxied)"""
    import json as _json
    args = _json.loads(props) if props else {}
    args["name"] = name
    return SIDECAR.call_tool("set_sprite", args)


def sb3_add_sprite(name: str, props: str = "{}") -> str:
    """Add a sprite; props is a JSON object string. (proxied)"""
    import json as _json
    args = _json.loads(props) if props else {}
    args["name"] = name
    return SIDECAR.call_tool("add_sprite", args)


def sb3_remove_sprite(name: str) -> str:
    """Remove a sprite. (proxied)"""
    return SIDECAR.call_tool("remove_sprite", {"name": name})


def sb3_rename_target(name: str, newName: str) -> str:
    """Rename a sprite/stage target. (proxied)"""
    return SIDECAR.call_tool("rename_target", {"name": name, "newName": newName})


def sb3_set_stage(props: str = "{}") -> str:
    """Set stage props; props is a JSON object string. (proxied)"""
    import json as _json
    args = _json.loads(props) if props else {}
    return SIDECAR.call_tool("set_stage", args)


def sb3_set_variable(target: str, name: str, value: str = "") -> str:
    """Set/create a variable on a target. (proxied)"""
    return SIDECAR.call_tool("set_variable", {"target": target, "name": name, "value": value})


def sb3_delete_variable(target: str, name: str) -> str:
    """Delete a variable. (proxied)"""
    return SIDECAR.call_tool("delete_variable", {"target": target, "name": name})


def sb3_set_list(target: str, name: str, items: str = "[]") -> str:
    """Set/create a list on a target; items is a JSON array string. (proxied)"""
    import json as _json
    return SIDECAR.call_tool("set_list", {"target": target, "name": name, "items": _json.loads(items)})


def sb3_delete_list(target: str, name: str) -> str:
    """Delete a list. (proxied)"""
    return SIDECAR.call_tool("delete_list", {"target": target, "name": name})


def sb3_add_broadcast(name: str) -> str:
    """Add a broadcast message. (proxied)"""
    return SIDECAR.call_tool("add_broadcast", {"name": name})


def sb3_list_comments() -> str:
    """List sprite comments. (proxied)"""
    return SIDECAR.call_tool("list_comments", {})


def sb3_add_comment(target: str, text: str, x: int = 0, y: int = 0) -> str:
    """Add a sprite comment. (proxied)"""
    return SIDECAR.call_tool("add_comment", {"target": target, "text": text, "x": x, "y": y})


def sb3_set_comment(comment_id: str, text: str) -> str:
    """Edit a sprite comment. (proxied)"""
    return SIDECAR.call_tool("set_comment", {"id": comment_id, "text": text})


def sb3_remove_comment(comment_id: str) -> str:
    """Remove a sprite comment. (proxied)"""
    return SIDECAR.call_tool("remove_comment", {"id": comment_id})


def sb3_add_costume(target: str, name: str, path: str, dataFormat: str = "") -> str:
    """Add a costume from a file. (proxied)"""
    args = {"target": target, "name": name, "path": path}
    if dataFormat:
        args["dataFormat"] = dataFormat
    return SIDECAR.call_tool("add_costume", args)


def sb3_remove_costume(target: str, name: str) -> str:
    """Remove a costume. (proxied)"""
    return SIDECAR.call_tool("remove_costume", {"target": target, "name": name})


def sb3_add_sound(target: str, name: str, path: str, dataFormat: str = "") -> str:
    """Add a sound from a file. (proxied)"""
    args = {"target": target, "name": name, "path": path}
    if dataFormat:
        args["dataFormat"] = dataFormat
    return SIDECAR.call_tool("add_sound", args)


def sb3_remove_sound(target: str, name: str) -> str:
    """Remove a sound. (proxied)"""
    return SIDECAR.call_tool("remove_sound", {"target": target, "name": name})


def sb3_reload(path: str = "") -> str:
    """Load an .sb3 from disk in TurboWarp Desktop via bridge. (proxied)"""
    return SIDECAR.call_tool("reload", {"path": path} if path else {})


def sb3_run_project() -> str:
    """Green flag in TurboWarp Desktop via bridge. (proxied)"""
    return SIDECAR.call_tool("run_project", {})


def sb3_stop_project() -> str:
    """Stop in TurboWarp Desktop via bridge. (proxied)"""
    return SIDECAR.call_tool("stop_project", {})


def sb3_vm_load() -> str:
    """Load the open project into the headless VM. (proxied)"""
    return SIDECAR.call_tool("vm_load", {})


def sb3_vm_green_flag() -> str:
    """Press green flag in the headless VM. (proxied)"""
    return SIDECAR.call_tool("vm_green_flag", {})


def sb3_vm_run(seconds: float = 0, frames: int = 0, untilIdle: bool | None = None, paced: bool | None = None) -> str:
    """Advance the headless VM; returns state + event timeline. (proxied)

    Budgets (`seconds`/`frames`) are omitted when 0 so the sidecar's own
    defaults apply (its zod schema rejects an explicit 0), and `untilIdle` /
    `paced` are only forwarded when set (None = sidecar default).
    `paced=False` runs the budget back-to-back with no per-frame sleep, which
    is what deterministic step-debug wants.
    """
    args: dict = {}
    if seconds:
        args["seconds"] = seconds
    if frames:
        args["frames"] = frames
    if untilIdle is not None:
        args["untilIdle"] = untilIdle
    if paced is not None:
        args["paced"] = paced
    return SIDECAR.call_tool("vm_run", args)


def sb3_vm_stop() -> str:
    """Stop all scripts in the headless VM. (proxied)"""
    return SIDECAR.call_tool("vm_stop", {})


def sb3_vm_state() -> str:
    """Snapshot headless VM state. (proxied)"""
    return SIDECAR.call_tool("vm_state", {})


def sb3_vm_input(keys: str = "", mouseX: int = 0, mouseY: int = 0, mouseDown: bool = False, answer: str = "") -> str:
    """Feed keyboard/mouse/answer input to the headless VM. (proxied)

    `keys` accepts a JSON array of {key, isDown?} objects or a single key
    name (wrapped to a full tap). Anything else that parses to a non-list
    raises ValueError before touching the sidecar — the sidecar's zod
    schema would reject it anyway, but failing here gives the shorter,
    proxy-attributed error.
    """
    import json as _json
    args: dict = {"mouseX": mouseX, "mouseY": mouseY, "mouseDown": mouseDown}
    if keys:
        try:
            parsed = _json.loads(keys)
        except Exception:
            parsed = [{"key": keys}]
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError("keys must be a JSON array of {key, isDown?} or a single key name")
        args["keys"] = parsed
    if answer:
        args["answer"] = answer
    return SIDECAR.call_tool("vm_input", args)


def sb3_vm_threads() -> str:
    """Live thread inspector: target, clone flag, hat, stack, status. (proxied)"""
    return SIDECAR.call_tool("vm_threads", {})


def sb3_vm_monitors() -> str:
    """Full monitor table (visible or not): label, opcode, value, mode. (proxied)"""
    return SIDECAR.call_tool("vm_monitors", {})


def sb3_vm_step_frame() -> str:
    """Step exactly one frame; returns before/after + delta. (proxied)"""
    return SIDECAR.call_tool("vm_step_frame", {})


def sb3_vm_seed(seed: int | None = None) -> str:
    """Deterministic PRNG seed for operator_random; None restores Math.random. (proxied)"""
    return SIDECAR.call_tool("vm_seed", {"seed": seed})


def sb3_vm_watch(name: str = "", target: str = "") -> str:
    """Poll-and-diff variable watcher: old/new/changed per key. (proxied)"""
    args: dict = {}
    if name:
        args["name"] = name
    if target:
        args["target"] = target
    return SIDECAR.call_tool("vm_watch", args)


def sb3_vm_stub_calls() -> str:
    """Recorded pen/sound stub calls since load. (proxied)"""
    return SIDECAR.call_tool("vm_stub_calls", {})


def sb3_vm_pen_png() -> str:
    """Pen raster as PNG base64 + non-transparent pixel count. (proxied)"""
    return SIDECAR.call_tool("vm_pen_png", {})


def sb3_vm_mix_wav() -> str:
    """Offline sound mix as WAV base64 + event count + seconds. (proxied)"""
    return SIDECAR.call_tool("vm_mix_wav", {})


def sb3_vm_run_until(seconds: float = 0, chunkFrames: int = 0, until: str = "") -> str:
    """Run the headless VM on virtual time until a predicate fires. (proxied)

    `until` is a JSON object string, e.g.
    `{"broadcastSeen": ["GameStart"]}`, `{"varEquals": [{"name": "Score",
    "value": 100}]}`, `{"threadsIdle": true}` — fires when ANY entry matches
    (OR). Budgets omitted when 0 so sidecar defaults apply. Replaces N×
    `sb3_vm_run` poll loops with one call.
    """
    import json as _json
    args: dict = {}
    if seconds:
        args["seconds"] = seconds
    if chunkFrames:
        args["chunkFrames"] = chunkFrames
    if until:
        args["until"] = _json.loads(until)
    return SIDECAR.call_tool("vm_run_until", args)


def sb3_vm_poke(variables: str = "", lists: str = "", sprites: str = "") -> str:
    """Set live VM state (fault injection mid-wave). (proxied)

    Each arg is a JSON array string: `variables` entries
    `{target?, name, value}`, `lists` entries `{target?, name, items}`,
    `sprites` entries `{target, x?, y?, direction?, size?, visible?,
    costume?}`. Unknown target/variable/list/costume is an error.
    """
    import json as _json
    args: dict = {}
    if variables:
        args["variables"] = _json.loads(variables)
    if lists:
        args["lists"] = _json.loads(lists)
    if sprites:
        args["sprites"] = _json.loads(sprites)
    return SIDECAR.call_tool("vm_poke", args)


def sb3_vm_clones() -> str:
    """Live clone census: name, pose, costume, sprite-locals per clone. (proxied)"""
    return SIDECAR.call_tool("vm_clones", {})


def sb3_find_blocks(opcode: str = "", fieldValue: str = "", inputName: str = "",
                    hat: bool | None = None, limit: int = 0) -> str:
    """Query every target's blocks map in the open project. (proxied)

    Filters combine (AND): opcode substring, field value, input name,
    topLevel-only. Needs an open project, not the VM.
    """
    args: dict = {}
    if opcode:
        args["opcode"] = opcode
    if fieldValue:
        args["fieldValue"] = fieldValue
    if inputName:
        args["inputName"] = inputName
    if hat is not None:
        args["hat"] = hat
    if limit:
        args["limit"] = limit
    return SIDECAR.call_tool("find_blocks", args)


def sb3_validate_blocks(target: str = "") -> str:
    """Validate target blocks against the opcode catalog. (proxied)

    Omit target for all targets. Advisory — custom extension blocks ignored.
    """
    return SIDECAR.call_tool("validate_blocks", {"target": target} if target else {})


def sb3_screenshot() -> str:
    """Capture the live TurboWarp stage as PNG (base64-wrapped note). (proxied)"""
    return SIDECAR.call_tool("screenshot", {})


def sb3_screenshot_jpeg(quality: int = 80) -> str:
    """Capture the live stage as compressed JPEG. (proxied)"""
    return SIDECAR.call_tool("screenshot_jpeg", {"quality": quality})


SB3_TOOL_DEFS = [
    sb3_open_project, sb3_save_project, sb3_project_info, sb3_scratch_login,
    sb3_open_scratch_project, sb3_push_to_scratch, sb3_share_project,
    sb3_list_sprites, sb3_get_target, sb3_get_target_json, sb3_list_blocks,
    sb3_get_block_schema, sb3_enable_extension, sb3_patch_target,
    sb3_set_sprite, sb3_add_sprite, sb3_remove_sprite, sb3_rename_target,
    sb3_set_stage, sb3_set_variable, sb3_delete_variable, sb3_set_list,
    sb3_delete_list, sb3_add_broadcast, sb3_list_comments, sb3_add_comment,
    sb3_set_comment, sb3_remove_comment, sb3_add_costume, sb3_remove_costume,
    sb3_add_sound, sb3_remove_sound, sb3_reload, sb3_run_project,
    sb3_stop_project, sb3_vm_load, sb3_vm_green_flag, sb3_vm_run,
    sb3_vm_stop, sb3_vm_state, sb3_vm_input, sb3_vm_threads,
    sb3_vm_monitors, sb3_vm_step_frame, sb3_vm_seed, sb3_vm_watch,
    sb3_vm_stub_calls, sb3_vm_pen_png, sb3_vm_mix_wav,
    sb3_vm_run_until, sb3_vm_poke, sb3_vm_clones,
    sb3_find_blocks, sb3_validate_blocks,
    sb3_screenshot, sb3_screenshot_jpeg,
]
