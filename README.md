# scratch-unified-mcp

The first standalone headless Scratch VM over MCP — plus everything needed to build, playtest, and publish Scratch projects without opening a browser.

117 tools. One stdio command. Zero name collisions.

```
MCP client ──stdio──▶ python3 -m scratch_unified
                        ├── social_*   website + social graph (scratchattach)
                        ├── project_*  goboscript text-authoring loop
                        ├── spy_*      blocks ↔ real Python (ScratchPy)
                        └── sb3_*      block surgery + headless VM control loop (vendored Node sidecar fork)
```

## Why this exists

Three Scratch tool ecosystems each covered part of the loop — website API, block-level editing with a headless VM, Python-to-blocks — but none covered all of it, and switching between three servers with three naming schemes killed momentum. This merges all three behind one transport.

The piece that didn't exist anywhere else: a **headless TurboWarp scratch-vm you can drive and inspect over MCP**. Load a `.sb3`, press green flag, click sprites, step frames, read threads, watch variables — all from tool calls, all inspectable.

## Quickstart

One command, macOS / Linux / Windows (Git Bash, MSYS2, WSL) — needs
Python ≥ 3.12 and Node ≥ 18 (both checked up front):

```bash
git clone https://github.com/x3vu/scratch-unified-mcp.git && cd scratch-unified-mcp
./install.sh
```

That installs the Python package (`scratch-unified` on PATH), the
sidecar's npm deps (unlocks all `sb3_*` tools), and verifies both.
Flags: `--no-sidecar` (skip npm deps), `--no-verify` (skip checks),
`--prefix DIR` (portable pip target). `install.ps1` is the same flow
for native PowerShell. Requires nothing but this repo — the ScratchPy
core and the VM sidecar are vendored; the old `upstream-*` clones are
gone (see "Layout").

Manual install (same steps, no script):

```bash
pip install .
npm install --no-audit --no-fund --legacy-peer-deps ./scratch_unified/sidecar
```

Then point any MCP client at it:

```jsonc
// MCP client config — easiest: the installed entry point
{ "mcpServers": { "scratch-unified": {
  "command": "scratch-unified",
  "env": {
    "SCRATCH_MCP_DATA_DIR": "/path/to/scratch-unified-mcp/.sessions"
    // "SCRATCH_MCP_BRIDGE_PORT": "9060"  // only if something else holds 9060
  },
  "timeout": 600000
} } }
```

```jsonc
// …or without installing: repo checkout + interpreters
{ "mcpServers": { "scratch-unified": {
  "command": "python3",
  "args": ["-m", "scratch_unified"],
  "cwd": "/path/to/scratch-unified-mcp",
  "env": {
    "PYTHONPATH": "/path/to/scratch-unified-mcp",
    "SCRATCH_MCP_DATA_DIR": "/path/to/scratch-unified-mcp/.sessions"
  },
  "timeout": 600000
} } }
```

## The headless VM loop

Six core tools, debug tools, media tools, and five live-run tools, all proxied to a lazily-spawned Node sidecar (TurboWarp `scratch-vm`, interpreted mode, stdout muted so MCP framing stays clean). The sidecar is a vendored fork at `scratch_unified/sidecar/` (upstream `scratch4js` @ `cb1669aa`, gitignored/read-only) — VM improvements land in the fork, never upstream:

| Tool | What it does |
|---|---|
| `sb3_open_project` / `sb3_save_project` | Load / write `.sb3` (saves live-reload TurboWarp Desktop) |
| `sb3_vm_load` | Load the open project into a fresh VM |
| `sb3_vm_green_flag` | Press green flag (clears bubbles, question, errors) |
| `sb3_vm_run` | Advance N seconds/frames, paced or flat-out. Returns state + ordered event timeline (`say`, `broadcast`, `question`/`answer`, errors) |
| `sb3_vm_state` | Snapshot now: targets (x/y/vars/lists/costume), monitors, bubbles, question, thread count, errors |
| `sb3_vm_input` | Keys, mouse position/clicks (stage coords), `ask` answers. Includes a click shim so `when this sprite clicked` fires headless |
| `sb3_vm_threads` | **Every live thread**: target, clone flag, starting hat, stack depth, status (`running`/`yield`/`promise-wait`/`done`), kill flag |
| `sb3_vm_monitors` | Full monitor table (visible or not) — watch a variable without pixels |
| `sb3_vm_step_frame` | Exactly one frame + before/after counts + delta (new threads, events that tick) |
| `sb3_vm_seed` | Deterministic PRNG (mulberry32 over `Math.random`; scratch-vm has no seedable RNG). Same seed, same run |
| `sb3_vm_watch` | Poll-and-diff variable watcher: old/new/changed per key, per-clone capable |
| `sb3_vm_stub_calls` | Recorded pen/sound stub calls since load |
| `sb3_vm_pen_png` | Pen raster (480×360 software canvas) as PNG base64 + pixel count. Strokes only |
| `sb3_vm_mix_wav` | Offline sound mix as WAV base64: every play at its timer offset, volume/pitch approximate |
| `sb3_vm_run_until` | Run on virtual time until a predicate fires (`varEquals` / `broadcastSeen` / `threadsIdle`, OR) — one call replaces N× `vm_run` polls |
| `sb3_vm_poke` | Write live VM state mid-run: variables/lists by target, sprite pose/costume. Fault injection the disk-editing `set_variable` can't do |
| `sb3_vm_clones` | Clone census: pose, costume, sprite-locals per live clone |
| `sb3_find_blocks` | Query every target's blocks by opcode substring / field value / input name / hat-only |
| `sb3_validate_blocks` | Catalog validator on demand: unknown opcodes (did-you-mean), bad inputs, disabled extensions. Advisory, custom blocks ignored |

Headless gaps are patched, not hidden: distance-based touching fallback (no renderer means every `touching` returns `false` upstream), sprite-click shim, broadcast logging via wrapped `startHats`, interpreter mode to dodge a JIT `pickrandom` false-alarm. Details: `scratch_unified/sidecar/src/runtime.js`.

## Tool census

`social_*` 20 · `project_*` 18 · `spy_*` 14 · `sb3_*` 65 (51 proxied + 9 native + 5 new) = **117**. Full per-tool reference: `docs/IDENTIFIERS.md`. Architecture: `docs/ARCHITECTURE.md`.

## Layout

```
scratch_unified/          the server (this is what runs)
  server.py               FastMCP app + main() (--help/--version/--list-tools)
  vendor_uu/              uukelele/scratch-mcp, vendored (social + projects)
  vendor_spy/             ScratchPy Studio core, vendored (blocks↔Python, MIT)
  sidecar/                vendored VM sidecar fork (editable; FORK.md + package.json)
  spy_loader.py           headless ScratchPy import (tkinter stubbed if absent)
  spy_tools.py            14 spy_* wrappers, one shared .spy server
  node_bridge.py          lazy Node sidecar transport (newline-delimited JSON-RPC, TIMEOUT-guarded)
  typed_proxy.py          typed sb3_* proxies (FastMCP rejects **kwargs)
  sb3_extra.py            git unpack/pack/diff, studio/remix/favorites, cloud vars
install.sh / install.ps1  one-command setup (sh / PowerShell) + verification
bin/scratch-unified       thin launcher (used when pip dir is on PATH but entry point isn't)
scripts/smoke_sidecar.py  post-install sidecar handshake check
docs/                     ARCHITECTURE, IDENTIFIERS
```

Everything the server needs ships in this repo (vendored `vendor_uu/`,
`vendor_spy/`, `sidecar/src/`); `npm install` inside `sidecar/` is the
only network step, and it's optional (without it only `sb3_*` tools
report unavailable). Sessions persist as session IDs only, never
passwords.

## Credits

- [uukelele/scratch-mcp](https://github.com/uukelele/scratch-mcp) (MIT) — social + goboscript core
- [playforge-coding/scratch4js](https://github.com/playforge-coding/scratch4js) (MPL-2.0) — sb3/VM/bridge core
- [ZDStudios/scratchpy-studio](https://github.com/ZDStudios/scratchpy-studio) (MIT) — blocks↔Python core
- [TurboWarp/scratch-vm](https://github.com/TurboWarp/scratch-vm) — the headless engine
- [scratchattach](https://github.com/TimMcCool/scratchattach), [goboscript](https://github.com/aspizu/goboscript)
