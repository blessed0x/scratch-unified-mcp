"""Lazy stdio proxy to the scratch4js Node MCP server (vendored sidecar fork).

The Node server (`scratch_unified/sidecar/src/index.js`, forked from
upstream-scratch4js @ cb1669aa — see `scratch_unified/sidecar/FORK.md`) owns
capabilities with no Python equivalent: JSON-Patch sb3 editing, the
scratch-vm block catalog/schema, the headless TurboWarp VM control loop, and
the TurboWarp Desktop live-reload bridge + screenshots.

Pattern: spawn `node index.js` once, speak MCP over stdio
(initialize -> notifications/initialized -> tools/call). The sb3_*
tools live as typed functions in `typed_proxy.py` (FastMCP rejects **kwargs
tools); this module owns the subprocess transport. If Node/deps are missing,
every sb3_* tool raises a clear "sidecar unavailable" message instead of
breaking startup.

FRAMING NOTE (2026-09-05): the scratch4js workspace resolves
@modelcontextprotocol/sdk to >= 1.10, where the stdio transport is
NEWLINE-DELIMITED JSON (serializeMessage = JSON.stringify(msg) + '\n', and
the read buffer splits on '\n'). The legacy Content-Length framing silently
breaks the handshake: the sidecar tries to JSON.parse the header line and
never answers initialize, which deadlocks every sb3_* call. If the workspace
is ever pinned back to an SDK < 1.10 (Content-Length era), this transport
must flip back.
"""
import atexit
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

# Editable sidecar fork (see scratch_unified/sidecar/FORK.md). Never point
# this back at upstream-scratch4js: that tree is gitignored/read-only and a
# re-clone would silently drop every VM improvement made for this project.
NODE_INDEX = Path(__file__).resolve().parent / "sidecar" / "src" / "index.js"
TIMEOUT = 120.0
UNAVAILABLE = (
    "Node sidecar unavailable (need node >= 18 plus sidecar deps: "
    "run `./install.sh` or `npm install` in scratch_unified/sidecar). "
    "social_*, project_*, and spy_* tools are unaffected."
)


class NodeSidecar:
    """Single lazy Node subprocess speaking MCP over stdio."""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._buf = b""

    def _fail(self, reason):
        raise RuntimeError("sb3_* tool failed: " + reason + " " + UNAVAILABLE)

    def _teardown(self):
        """Terminate + reap the child (if any) and drop buffered bytes.

        Every failure path used to just do `self._proc = None`, which dropped
        the Popen without terminating the child: `node` kept running holding
        its stdout pipe, and each retry leaked another process. The stale
        `_buf` mattered too — a partial line from the dead process would be
        prepended to the restarted sidecar's first reply, so the reply would
        either fail to parse or correlate against the wrong request id.
        Idempotent, and safe to call while holding `self._lock`.
        """
        proc, self._proc = self._proc, None
        self._buf = b""
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass

    def _ensure_started(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        # Reap a previous child (exited on its own, or already dropped by a
        # failure path) and clear any buffered bytes before spawning a fresh
        # one — otherwise the old process's leftovers poison the new session.
        self._teardown()
        if shutil.which("node") is None:
            self._fail("node binary not found.")
        if not NODE_INDEX.is_file():
            self._fail("Node server missing at %s." % NODE_INDEX)
        try:
            # start_new_session: detach the sidecar from this process group so
            # a headless run launched from a terminal tool call is not SIGHUPed
            # when the tool's shell session tears down mid-run.
            self._proc = subprocess.Popen(
                ["node", str(NODE_INDEX)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            self._teardown()
            self._fail("could not spawn node: %s" % exc)
        try:
            # NOTE: the scratch4js sidecar only answers an initialize that
            # advertises the LEGACY protocol version (2024-11-05). Advertising
            # 2025-06-18 is silently ignored and every sb3_* call times out.
            self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "scratch-unified", "version": "1.0.0"},
            })
            self._notify("notifications/initialized")
        except Exception as exc:
            self._teardown()
            self._fail("Node server did not answer initialize: %s" % exc)

    def _write(self, obj):
        # Newline-delimited JSON-RPC framing (SDK >= 1.10). Each message is
        # exactly one JSON document terminated by a single '\n'.
        payload = json.dumps(obj).encode("utf-8") + b"\n"
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()

    def _read_line(self):
        # Read via the raw fd with non-blocking os.read. select() on the
        # BufferedReader never reported the pipe readable even with a reply
        # waiting (observed 2026-09-05: select timed out for the full window
        # while os.read on the same fd returned the bytes instantly), so
        # select is NOT used here. The non-blocking poll gives a deadline that
        # actually fires when the sidecar is alive but silent, and a plain
        # blocking read can never reach its timeout checks.
        fd = self._proc.stdout.fileno()
        os.set_blocking(fd, False)
        deadline = time.monotonic() + TIMEOUT
        # Loop rather than recurse on blank keep-alive lines: the recursive
        # form let a chatty sidecar blow the Python stack.
        while True:
            while b"\n" not in self._buf:
                if time.monotonic() >= deadline:
                    self._teardown()
                    raise RuntimeError(
                        "Node sidecar timeout after %.1fs waiting for a message" % TIMEOUT)
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    self._buf += chunk
                elif self._proc.poll() is not None:
                    self._teardown()
                    raise RuntimeError("node stdout closed")
                else:
                    time.sleep(0.01)
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if line.strip():
                return json.loads(line.decode("utf-8"))
            # Blank keep-alive line: keep waiting for the next real message.

    def _rpc(self, method, params=None):
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
            while True:
                msg = self._read_line()
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(str(msg["error"]))
                    return msg.get("result")
                # ignore async notifications/log messages

    def _notify(self, method):
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method})

    def call_tool(self, name, arguments):
        self._ensure_started()
        try:
            result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        except Exception as exc:
            self._teardown()
            raise RuntimeError("Node sidecar call %s failed: %s" % (name, exc))
        if isinstance(result, dict) and result.get("isError"):
            texts = [b.get("text", "") for b in result.get("content", []) if isinstance(b, dict)]
            raise RuntimeError("Node tool %s error: %s" % (name, " ".join(texts)[:2000]))
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            return "\n".join(str(b.get("text", b)) for b in result["content"])
        # Bumped from 8000 to 50000 chars so long event timelines from
        # sb3_vm_state / sb3_vm_run don't get silently clipped. Append a
        # visible marker if we did clip so callers can detect it.
        s = json.dumps(result)
        if len(s) > 50000:
            return s[:50000] + "\n... [truncated at 50000 chars; full result not returned]"
        return s


SIDECAR = NodeSidecar()

# The sidecar is spawned with `start_new_session=True` (so a headless run is not
# SIGHUPed when its shell tears down), which also means nothing reaps it when
# this process exits. Without this hook every MCP session — and every headless
# run — leaves an orphaned `node` holding its pipes (and possibly the bridge
# port, causing the next run's EADDRINUSE).
atexit.register(SIDECAR._teardown)


def register_sb3_tools(mcp):
    """Register the typed sb3_* proxy tools on the FastMCP app."""
    from .typed_proxy import SB3_TOOL_DEFS

    for fn in SB3_TOOL_DEFS:
        mcp.tool(fn)
