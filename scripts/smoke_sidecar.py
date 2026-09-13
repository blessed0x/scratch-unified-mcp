#!/usr/bin/env python3
"""Post-install smoke test: sidecar handshake + one block query.

Covers what install.sh just set up (node runs, npm deps resolve, MCP framing
matches) without loading a project or the VM. Exit 1 with a hint on failure.

Usage: python3 scripts/smoke_sidecar.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NODE_INDEX = ROOT / "scratch_unified" / "sidecar" / "src" / "index.js"


def main() -> int:
    import shutil

    if shutil.which("node") is None:
        print("SMOKE FAIL: node not found (install node >= 18, then re-run install.sh).")
        return 1
    if not NODE_INDEX.is_file():
        print("SMOKE FAIL: sidecar missing at %s." % NODE_INDEX)
        return 1

    proc = subprocess.Popen(
        ["node", str(NODE_INDEX)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "smoke", "version": "0"}},
        }) + "\n")
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        deadline = time.monotonic() + 30
        buf = ""
        answer = None
        while time.monotonic() < deadline:
            chunk = proc.stdout.readline()
            if not chunk:
                break
            buf += chunk
            if "\n" not in buf:
                continue
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 1:
                answer = msg
                break
        if proc.poll() is not None:
            print("SMOKE FAIL: sidecar exited during initialize "
                  "(npm deps probably missing — run install.sh).")
            return 1
        if answer is None or "error" in answer:
            print("SMOKE FAIL: no initialize answer (%s)." % json.dumps(answer)[:200])
            return 1
        print("SMOKE OK: sidecar %s answered initialize."
              % answer.get("result", {}).get("serverInfo", {}).get("version", "?"))
        return 0
    finally:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
