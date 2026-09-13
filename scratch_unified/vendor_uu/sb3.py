import json, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx
from fastmcp.exceptions import ToolError

ASSET_URL = "https://assets.scratch.mit.edu/internalapi/asset/{}/get/"

COMMON_DEFAULTS = {
    "currentCostume": 0,
    "volume": 100,
    "layerOrder": 1,
    "variables": {},
    "lists": {},
    "broadcasts": {},
    "blocks": {},
    "comments": {},
    "costumes": [],
    "sounds": [],
}

STAGE_DEFAULTS = {
    "tempo": 60,
    "videoTransparency": 50,
    "videoState": "on",
    "textToSpeechLanguage": None,
}

SPRITE_DEFAULTS = {
    "visible": True,
    "x": 0,
    "y": 0,
    "size": 100,
    "direction": 90,
    "draggable": False,
    "rotationStyle": "all around",
}


def fetch(session, project_id: int, dest: Path) -> Path:
    try:
        project_json = json.loads(session.connect_project(project_id).get_json())
    except Exception as error:
        raise ToolError(
            f"Could not download project {project_id} as "
            f"'{session.username}': {type(error).__name__}: {error}"
        ) from error

    wanted: list[str] = []
    for target in project_json.get("targets", []):
        for asset in list(target.get("costumes", [])) + list(target.get("sounds", [])):
            md5ext = asset.get("md5ext")
            if not md5ext:
                if asset.get("assetId") and asset.get("dataFormat"): md5ext = f"{asset['assetId']}.{asset['dataFormat']}"
            if md5ext and md5ext not in wanted: wanted.append(md5ext)

    dest.parent.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []

    def grab(md5ext: str) -> tuple[str, Optional[bytes], Optional[str]]:
        try:
            res = httpx.get(ASSET_URL.format(md5ext), timeout=30)
            res.raise_for_status()
            return md5ext, res.content, None
        except Exception as error: return md5ext, None, type(error).__name__

    fetched: dict[str, bytes] = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for md5ext, data, error in pool.map(grab, wanted):
                if data is None: missing.append(f"{md5ext} ({error})")
                else: fetched[md5ext] = data

    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', json.dumps(project_json))
        for md5ext in wanted:
            if md5ext in fetched: archive.writestr(md5ext, fetched[md5ext])

    if missing:
        raise ToolError(
            f"Downloaded project {project_id} but {len(missing)} asset(s) could "
            f"not be fetched, so the .sb3 would be incomplete: "
            f"{', '.join(missing[:5])}"
        )

    return dest


def _unique(name: str, taken: set[str]) -> str:
    n = 2
    while f"{name}_{n}" in taken: n += 1
    return f"{name}_{n}"


def _dedupe(target: dict, reserved: set[str]) -> list[str]:
    notes: list[str] = []
    renames: dict[str, str] = {}  # variable id: new name

    for kind in ('variables', 'lists'):
        entries = target.get(kind) or {}
        seen: set[str] = set()
        for var_id, value in entries.items():
            if not isinstance(value, list) or not value: continue
            name = value[0]
            if name in seen or name in reserved:
                taken = seen | reserved | {v[0] for v in entries.values() if v}
                new = _unique(name, taken)
                value[0] = new
                renames[var_id] = new
                seen.add(new)
                notes.append(
                    f"{target.get('name')!r}: renamed duplicate {kind[:-1]} "
                    f"{name!r} to {new!r}"
                )
            else: seen.add(name)
        reserved |= seen

    if not renames: return notes

    for block in (target.get("blocks") or {}).values():
        if isinstance(block, list):
            if len(block) >= 3 and block[0] in (12, 13) and block[2] in renames: block[1] = renames[block[2]]
            continue
        for field in (block.get("fields") or {}).values():
            if isinstance(field, list) and len(field) >= 2 and field[1] in renames: field[0] = renames[field[1]]

    return notes


ARG_TOKENS = ("%s", "%b", "%n")


def _proc_label(proccode: str) -> str:
    out = proccode
    for token in ARG_TOKENS: out = out.replace(token, '')
    return " ".join(out.split())


def _suffix_proccode(proccode: str, suffix: str) -> str:
    tokens = proccode.split(" ")
    for i, token in enumerate(tokens):
        if token and token not in ARG_TOKENS:
            tokens[i] = token + suffix
            return " ".join(tokens)

    return f"proc{suffix} {proccode}"


def _dedupe_procs(target: dict) -> list[str]:
    blocks = target.get("blocks") or {}
    prototypes = [
        b for b in blocks.values()
        if isinstance(b, dict)
        and b.get("opcode") == "procedures_prototype"
        and isinstance(b.get("mutation"), dict)
    ]

    notes: list[str] = []
    used: set[str] = set()
    renames: dict[str, str] = {}  # old proccode: new proccode

    for proto in prototypes:
        proccode = proto["mutation"].get("proccode")
        if not isinstance(proccode, str): continue
        label = _proc_label(proccode)
        if label not in used:
            used.add(label)
            continue

        n = 2
        while True:
            candidate = _suffix_proccode(proccode, f"_{n}")
            if _proc_label(candidate) not in used: break
            n += 1

        used.add(_proc_label(candidate))
        renames[proccode] = candidate
        proto["mutation"]["proccode"] = candidate
        notes.append(
            f"{target.get('name')!r}: renamed custom block {proccode!r} to "
            f"{candidate!r} (goboscript cannot have two procedures named "
            f"{label!r})"
        )

    if renames:
        for block in blocks.values():
            if not isinstance(block, dict): continue
            if block.get("opcode") != "procedures_call": continue
            mutation = block.get("mutation")
            if isinstance(mutation, dict) and mutation.get("proccode") in renames: mutation["proccode"] = renames[mutation["proccode"]]

    return notes


def _image_size(data: bytes, fmt: str) -> Optional[tuple[float, float]]:
    if fmt == "svg":
        import re as _re

        head = data[:4096].decode("utf-8", "ignore")
        out = []
        for attr in ("width", "height"):
            m = _re.search(rf'<svg[^>]*?\b{attr}\s*=\s*"([0-9.]+)', head)
            if not m: return None
            out.append(float(m.group(1)))
        return out[0], out[1]

    if fmt == "png" and data[:8] == b"\x89PNG\r\n\x1a\n":
        import struct

        w, h = struct.unpack(">II", data[16:24])
        return float(w), float(h)

    return None


def _fill_asset_defaults(target: dict, assets: dict[str, bytes]) -> set[str]:
    filled: set[str] = set()

    for costume in target.get("costumes") or []:
        if not isinstance(costume, dict): continue
        fmt = costume.get("dataFormat")
        if "md5ext" not in costume and costume.get("assetId") and fmt:
            costume["md5ext"] = f"{costume['assetId']}.{fmt}"
            filled.add("md5ext")
        if fmt != "svg" and "bitmapResolution" not in costume:
            costume["bitmapResolution"] = 1
            filled.add("bitmapResolution")
        if "rotationCenterX" in costume and "rotationCenterY" in costume: continue
        size = _image_size(assets.get(costume.get("md5ext", ""), b""), fmt or "")
        if size is None: width = height = 0.0
        else: width, height = size
        costume.setdefault("rotationCenterX", width / 2)
        costume.setdefault("rotationCenterY", height / 2)
        filled.update({"rotationCenterX", "rotationCenterY"})

    for sound in target.get("sounds") or []:
        if not isinstance(sound, dict): continue
        fmt = sound.get("dataFormat")
        if "md5ext" not in sound and sound.get("assetId") and fmt:
            sound["md5ext"] = f"{sound['assetId']}.{fmt}"
            filled.add("md5ext")
        for key, value in (("rate", 48000), ("sampleCount", 0)):
            if key not in sound:
                sound[key] = value
                filled.add(key)

    return filled


NAME_MENUS = {
    "looks_switchcostumeto": ("COSTUME", "COSTUME", "costumes"),
    "looks_switchbackdropto": ("BACKDROP", "BACKDROP", "backdrops"),
    "looks_switchbackdroptoandwait": ("BACKDROP", "BACKDROP", "backdrops"),
    "sound_play": ("SOUND_MENU", "SOUND_MENU", "sounds"),
    "sound_playuntildone": ("SOUND_MENU", "SOUND_MENU", "sounds"),
}

MENU_KEYWORDS = {
    "next backdrop", "previous backdrop", "random backdrop",
    "next costume", "previous costume",
}


def _sync_field_names(targets: list) -> list[str]:
    names: dict[str, str] = {}
    for target in targets:
        if not isinstance(target, dict): continue
        for kind in ("variables", "lists"):
            for var_id, value in (target.get(kind) or {}).items():
                if isinstance(value, list) and value and isinstance(value[0], str):
                    names[var_id] = value[0]

    fixed = 0
    for target in targets:
        if not isinstance(target, dict): continue
        for block in (target.get("blocks") or {}).values():
            if isinstance(block, list):
                if len(block) >= 3 and block[0] in (12, 13):
                    current = names.get(block[2])
                    if current is not None and block[1] != current:
                        block[1] = current
                        fixed += 1
                continue
            if not isinstance(block, dict): continue
            for field_key, field in (block.get("fields") or {}).items():
                if field_key not in ("VARIABLE", "LIST"): continue
                if isinstance(field, list) and len(field) >= 2:
                    current = names.get(field[1])
                    if current is not None and field[0] != current:
                        field[0] = current
                        fixed += 1

    if not fixed: return []

    return [
        f"corrected {fixed} stale variable/list name(s) in blocks to match the "
        f"current declaration (Scratch resolves these by id, not by name)"
    ]


def _declare_dangling_refs(targets: list, stage: Optional[dict]) -> list[str]:
    if stage is None: return []

    declared: set[str] = set()
    for target in targets:
        if not isinstance(target, dict): continue
        for kind in ("variables", "lists"): declared |= set((target.get(kind) or {}).keys())

    missing: dict[str, tuple[str, str]] = {}  # id: (name, kind)

    def note_ref(var_id, name, kind):
        if (
            isinstance(var_id, str)
            and isinstance(name, str)
            and var_id not in declared
            and var_id not in missing
        ): missing[var_id] = (name, kind)

    for target in targets:
        if not isinstance(target, dict): continue
        for block in (target.get("blocks") or {}).values():
            if isinstance(block, list):
                # prim: [12, name, id] variable, [13, name, id] list.
                if len(block) >= 3 and block[0] in (12, 13): note_ref(block[2], block[1], "lists" if block[0] == 13 else "variables")
                continue
            if not isinstance(block, dict): continue
            for field_key, field in (block.get("fields") or {}).items():
                if field_key not in ("VARIABLE", "LIST"): continue
                if isinstance(field, list) and len(field) >= 2:
                    note_ref(
                        field[1], field[0],
                        "lists" if field_key == "LIST" else "variables",
                    )

    for var_id, (name, kind) in missing.items():
        bucket = stage.setdefault(kind, {})
        bucket[var_id] = [name, [] if kind == "lists" else 0]

    if not missing: return []

    listed = ", ".join(sorted(f"{n!r}" for n, _ in missing.values())[:6])
    return [
        f"declared {len(missing)} variable(s)/list(s) that blocks referenced but "
        f"nothing defined ({listed}); Scratch treats these as empty"
    ]


def _fix_mutations(target: dict) -> list[str]:
    fixed: set[str] = set()

    for block in (target.get("blocks") or {}).values():
        if not isinstance(block, dict): continue
        mutation = block.get("mutation")
        if not isinstance(mutation, dict): continue

        for key in ("warp", "hasnext"):
            if isinstance(mutation.get(key), bool):
                mutation[key] = "true" if mutation[key] else "false"
                fixed.add(key)

        for key in ("argumentids", "argumentnames", "argumentdefaults"):
            if isinstance(mutation.get(key), (list, dict)):
                mutation[key] = json.dumps(mutation[key])
                fixed.add(key)

    if not fixed: return []

    return [
        f"{target.get('name')!r}: coerced mutation field(s) "
        f"{', '.join(sorted(fixed))} to the string form the sb3 format requires"
    ]


def _drop_block(blocks: dict, block_id: str) -> None:
    block = blocks.get(block_id)
    if not isinstance(block, dict): return

    parent_id = block.get("parent")
    next_id = block.get("next")
    parent = blocks.get(parent_id) if parent_id else None

    if parent is not None:
        if parent.get("next") == block_id: parent["next"] = next_id

        for key, value in list((parent.get("inputs") or {}).items()):
            if isinstance(value, list) and len(value) >= 2 and value[1] == block_id:
                if next_id: value[1] = next_id
                else: parent["inputs"].pop(key, None)

    if next_id and isinstance(blocks.get(next_id), dict):
        following = blocks[next_id]
        if parent_id: following["parent"] = parent_id
        else:
            following["parent"] = None
            following["topLevel"] = True
            for coord in ("x", "y"):
                if coord in block: following[coord] = block[coord]

    for value in (block.get("inputs") or {}).values():
        if isinstance(value, list):
            for item in value[1:]:
                if isinstance(item, str) and item in blocks:
                    shadow = blocks[item]
                    if isinstance(shadow, dict) and shadow.get("shadow"):
                        blocks.pop(item, None)

    blocks.pop(block_id, None)


ARG_REPORTERS = ("argument_reporter_string_number", "argument_reporter_boolean")


def _orphan_arg_reporters(target: dict) -> list[str]:
    blocks = target.get("blocks") or {}

    def scope_of(block_id: str) -> Optional[set[str]]:
        seen = set()
        current = block_id
        while current and current not in seen:
            seen.add(current)
            block = blocks.get(current)
            if not isinstance(block, dict): return None
            parent = block.get("parent")
            if not parent:
                if block.get("opcode") != "procedures_definition": return None
                proto_input = (block.get("inputs") or {}).get("custom_block")
                if not (isinstance(proto_input, list) and len(proto_input) >= 2): return None
                proto = blocks.get(proto_input[1])
                if not isinstance(proto, dict): return None
                raw = (proto.get("mutation") or {}).get("argumentnames")
                try: names = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, TypeError): return None
                return set(names) if isinstance(names, list) else None
            current = parent
        return None

    doomed: list[tuple[str, str]] = []
    for block_id, block in blocks.items():
        if not isinstance(block, dict) or block.get("opcode") not in ARG_REPORTERS: continue
        name = ((block.get("fields") or {}).get("VALUE") or [None])[0]
        if not isinstance(name, str): continue
        scope = scope_of(block_id)
        if scope is not None and name in scope: continue
        doomed.append((block_id, name))

    removed = 0
    for block_id, _ in doomed:
        block = blocks.get(block_id)
        parent = blocks.get((block or {}).get("parent")) if block else None
        if isinstance(parent, dict):
            for key, value in list((parent.get("inputs") or {}).items()):
                if not (isinstance(value, list) and len(value) >= 2 and value[1] == block_id): continue
                if value[0] == 3 and len(value) >= 3: parent["inputs"][key] = [1, value[2]]
                else: parent["inputs"].pop(key, None)
        blocks.pop(block_id, None)
        removed += 1

    if not removed: return []

    from collections import Counter

    counts = Counter(name for _, name in doomed)
    listed = ", ".join(f"{n!r}x{c}" if c > 1 else repr(n) for n, c in counts.most_common(6))
    return [
        f"{target.get('name')!r}: neutralised {removed} argument reporter(s) used "
        f"outside their definition ({listed}); Scratch reports 0 for these"
    ]


def _drop_dead_menu_refs(target: dict, backdrops: set[str]) -> list[str]:
    blocks = target.get("blocks") or {}
    available = {
        "costumes": {c.get("name") for c in target.get("costumes") or [] if isinstance(c, dict)},
        "sounds": {s.get("name") for s in target.get("sounds") or [] if isinstance(s, dict)},
        "backdrops": backdrops,
    }

    doomed: list[tuple[str, str, str]] = []
    for block_id, block in blocks.items():
        if not isinstance(block, dict): continue
        spec = NAME_MENUS.get(block.get("opcode"))
        if spec is None: continue
        input_key, field_key, kind = spec

        value = (block.get("inputs") or {}).get(input_key)
        if not (isinstance(value, list) and value and value[0] == 1): continue
        menu = blocks.get(value[1]) if isinstance(value[1], str) else None
        if not isinstance(menu, dict): continue
        field = (menu.get("fields") or {}).get(field_key)
        if not (isinstance(field, list) and field and isinstance(field[0], str)): continue

        name = field[0]
        if name in MENU_KEYWORDS or name in available[kind]: continue

        try:
            float(name)
            continue
        except ValueError: ...

        doomed.append((block_id, block["opcode"], name))

    for block_id, _, _ in doomed: _drop_block(blocks, block_id)

    if not doomed: return []

    from collections import Counter

    counts = Counter(f"{opcode} -> {name!r}" for _, opcode, name in doomed)
    return [
        f"{target.get('name')!r}: removed {count} block(s) referencing a "
        f"non-existent asset ({what}); Scratch ignores these at runtime"
        for what, count in counts.most_common()
    ]


def normalise(
    project_json: dict, assets: Optional[dict[str, bytes]] = None
) -> tuple[dict, list[str]]:
    notes: list[str] = []
    targets = project_json.get("targets")
    if not isinstance(targets, list): raise ToolError("project.json has no 'targets' list; it is not a valid project.")

    filled: set[str] = set()
    for target in targets:
        if not isinstance(target, dict): continue
        defaults = dict(COMMON_DEFAULTS)
        defaults.update(STAGE_DEFAULTS if target.get("isStage") else SPRITE_DEFAULTS)
        for key, value in defaults.items():
            if key not in target:
                target[key] = json.loads(json.dumps(value))  # fresh copy
                filled.add(key)
        filled |= _fill_asset_defaults(target, assets or {})

    if filled:
        notes.append(
            "Filled in project.json keys that Scratch defaults but the "
            f"decompiler requires: {', '.join(sorted(filled))}."
        )

    stage = next((t for t in targets if isinstance(t, dict) and t.get("isStage")), None)

    notes += _sync_field_names(targets)
    notes += _declare_dangling_refs(targets, stage)
    global_names: set[str] = set()
    if stage is not None:
        notes += _dedupe(stage, set())
        for kind in ("variables", "lists"):
            global_names |= {
                v[0] for v in (stage.get(kind) or {}).values() if isinstance(v, list) and v
            }

    for target in targets:
        if isinstance(target, dict) and not target.get("isStage"): notes += _dedupe(target, set(global_names))

    for target in targets:
        if isinstance(target, dict):
            notes += _fix_mutations(target)
            notes += _dedupe_procs(target)

    backdrops = {
        c.get("name")
        for c in ((stage or {}).get("costumes") or [])
        if isinstance(c, dict)
    }

    for target in targets:
        if isinstance(target, dict):
            notes += _drop_dead_menu_refs(target, backdrops)
            notes += _orphan_arg_reporters(target)

    by_id: dict[str, str] = {}
    for target in targets:
        if not isinstance(target, dict): continue
        for kind in ("variables", "lists"):
            for var_id, value in (target.get(kind) or {}).items():
                if isinstance(value, list) and value:
                    by_id[var_id] = value[0]

    for monitor in project_json.get("monitors") or []:
        if not isinstance(monitor, dict): continue
        new = by_id.get(monitor.get("id"))
        if not new: continue
        params = monitor.get("params")
        if isinstance(params, dict):
            for key in ("VARIABLE", "LIST"):
                if key in params:
                    params[key] = new

    return project_json, notes


def rewrite(sb3: Path) -> list[str]:
    try:
        with zipfile.ZipFile(sb3) as archive: entries = [(n, archive.read(n)) for n in archive.namelist()]
    except (zipfile.BadZipFile, OSError) as error: raise ToolError(f"'{sb3}' is not a readable .sb3: {error}") from error

    original = next((data for name, data in entries if name == "project.json"), None)
    if original is None: raise ToolError(f"'{sb3}' has no project.json.")

    try: project_json = json.loads(original)
    except json.JSONDecodeError as error: raise ToolError(f"'{sb3}' has an unparseable project.json: {error}") from error

    project_json, notes = normalise(project_json, {name: data for name, data in entries if name != "project.json"})

    tmp = sb3.with_suffix(".sb3.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("project.json", json.dumps(project_json))
        for name, data in entries:
            if name != "project.json":
                out.writestr(name, data)
    tmp.replace(sb3)

    return notes


def read(sb3: Path) -> tuple[dict, list[tuple[str, bytes]]]:
    try:
        with zipfile.ZipFile(sb3) as archive:
            project_json = json.loads(archive.read("project.json"))
            assets = [
                (name, archive.read(name))
                for name in archive.namelist()
                if name != "project.json"
            ]
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise ToolError(
            f"'{sb3}' is not a readable .sb3: {type(error).__name__}: {error}. "
            "Rebuild with `project_build`."
        ) from error
    return project_json, assets