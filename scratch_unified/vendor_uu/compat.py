import base64
import datetime
import json
import re
import zlib

_NON_B64 = re.compile(rb"[^A-Za-z0-9_\-]")


def _decode_session_id(session_id: str):
    from scratchattach.utils import commons

    p1, p2, _ = session_id.split(":")

    cleaned = _NON_B64.sub(b"", p1.encode())
    cleaned += b"=" * (-len(cleaned) % 4)  # restore b64 padding
    raw = base64.urlsafe_b64decode(cleaned)

    try: raw = zlib.decompress(raw)
    except zlib.error: ...

    return (json.loads(raw), datetime.datetime.fromtimestamp(commons.b62_decode(p2)))


def _session_id_decoding_is_broken(session_module) -> bool:
    payload = json.dumps({"username": "probe", "token": "t", "_auth_user_id": "1"})
    p1 = base64.urlsafe_b64encode(zlib.compress(payload.encode())).decode().rstrip("=")
    probe = f".{p1}:1a:sig"

    try: data, _ = session_module.decode_session_id(probe)
    except Exception: return True

    return data.get("username") != "probe"


def apply() -> list[str]:
    applied: list[str] = []

    try: from scratchattach.site import session as session_module
    except Exception: return applied

    if _session_id_decoding_is_broken(session_module):
        session_module.decode_session_id = _decode_session_id
        applied.append(
            "patched scratchattach.site.session.decode_session_id: the installed "
            "version cannot read a zlib-compressed session id that is not "
            "wrapped in quotes, which breaks every hand-pasted session id"
        )

    return applied


APPLIED = apply()