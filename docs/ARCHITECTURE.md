# ARCHITECTURE — Scratch Unified MCP

## 1. Big picture

One Python FastMCP server owns a single stdio transport. Two upstream
codebases are vendored/imported in-process (uukelele, scratchpy); the third
(scratch4js/Node) runs as a lazily-spawned sidecar subprocess spoken to over
MCP-over-stdio with Content-Length framing.

```
MCP client (Claude Desktop / Inspector)
  │  stdio (JSON-RPC)
  ▼
scratch_unified/server.py  ── FastMCP("scratch-unified")
  ├── vendor_uu/social.py + projects.py   (native, scratchattach+goboscript)
  ├── spy_tools.py → spy_loader.py        (native, headless scratchpy core)
  ├── sb3_extra.py                        (native: git diff, studio, cloud)
  └── node_bridge.py → typed_proxy.py     (lazy sidecar → Node index.js)
```

Why this shape: 2 of 3 sources are Python, so Python hosts. scratch-vm has
no Python equivalent, so Node stays a subprocess instead of being ported.
FastMCP rejects `**kwargs` tools, so every proxied tool gets an explicit
typed function.

## 2. Module map

| File | Role | Key identifiers |
|---|---|---|
| `scratch_unified/__init__.py` | package marker + path constants | `__version__`, `ROOT`, `UPSTREAM_UU`, `UPSTREAM_JS`, `UPSTREAM_SPY`, `NODE_SERVER`, `SPY_FILE` |
| `scratch_unified/__main__.py` | `python -m scratch_unified` entry | `main()` (re-export from `server`) |
| `scratch_unified/server.py` | owns the app, registers families, serves | `mcp` (re-exported FastMCP app), `main(argv)` — restores sessions via `utils._restore()`, then `mcp.run(transport="stdio")` |
| `scratch_unified/vendor_uu/server.py` | the ONE FastMCP app object (vendored modules do `from .server import mcp`) | `mcp` |
| `scratch_unified/vendor_uu/social.py` | 20 `social_*` tools (website/social graph) | `social_connect_session`, `social_list_sessions`, `social_set_active_session`, `social_verify_session`, `social_forget_session`, `social_set_bio`, `social_set_whatimworkingon`, `social_set_pfp`, `social_get_user_info`, `social_get_project_info`, `social_search_projects`, `social_check_inbox`, `social_get_comments`, `social_get_comment_replies`, `social_post_comment`, `social_reply_to_comment`, `social_follow_user`, `social_like_project`, `social_add_project_to_studio`, `social_become_scratcher`; helpers `InboxResult`, `ProjectHit`, `ProjectSearch`, `ScratcherResult` |
| `scratch_unified/vendor_uu/projects.py` | 18 `project_*` tools (goboscript lifecycle + assets) | `project_new`, `project_open`, `project_download`, `project_list`, `project_select`, `project_info`, `project_close`, `project_list_assets`, `project_add_costume`, `project_add_sound`, `project_remove_asset`, `project_build`, `project_summary`, `project_save_to_cloud`, `project_set_thumbnail`, `project_editing_guide`, `project_goboscript_docs_help`, `project_check_toolchain`; types `ProjectSummary`, `DownloadResult`, `BuildResult`, `PublishResult`, `AssetList` |
| `scratch_unified/vendor_uu/utils.py` | session/project registries, scratchattach converters | `SESSIONS`, `ACTIVE`, `PERSISTED`, `_restore()`, `_persist()`, `active_ses()`, `maybe_ses()`, `me()`, `get_user()`, `get_project()`, `to_user()`, `to_project()`, `to_comment()`, `UserInfo`, `ProjectInfo`, `CommentInfo`, `CommentThread`, `CommentPage`, `BROWSERS` |
| `scratch_unified/vendor_uu/store.py` | JSON persistence (sessions + open projects) | `State`, `Record`, `Project`, `data_dir()`, `session_file()`, `read()`, `write()`, `clear()`; env `SCRATCH_MCP_DATA_DIR`, `SCRATCH_MCP_SESSION_FILE` |
| `scratch_unified/vendor_uu/goboscript.py` | Rust toolchain wrapper | called via `project_check_toolchain` / `project_build`; env `GOBOSCRIPT_BIN`, `SB2GS_BIN` |
| `scratch_unified/vendor_uu/sb3.py` | SB3 normalizer for real-world projects | `read()` (used by `project_download` decompile path) |
| `scratch_unified/vendor_uu/prompts.py` | editing-guide + docs text | backs `project_editing_guide`, `project_goboscript_docs_help` |
| `scratch_unified/vendor_uu/compat.py` | startup patch for scratchattach session decoding | `apply()`, `APPLIED`, `_decode_session_id()` |
| `scratch_unified/spy_loader.py` | headless import of `scratchpy_studio.py` (tkinter stub fallback) | `SPY_PATH`, `load_spy()` (cached) |
| `scratch_unified/spy_tools.py` | 14 `spy_*` tools, one shared `MCPServer` in temp dir | `SPY_TOOL_DEFS`, `register_spy_tools(mcp)`, `spy_open_project`, `spy_project_overview`, `spy_read_blocks`, `spy_read_code`, `spy_write_python`, `spy_import_python_file`, `spy_delete_file`, `spy_set_variable`, `spy_run`, `spy_list_block_types`, `spy_list_packages`, `spy_install_package`, `spy_add_package_blocks`, `spy_remove_package_blocks` |
| `scratch_unified/node_bridge.py` | lazy sidecar transport | `NODE_INDEX`, `TIMEOUT`, `UNAVAILABLE`, `NodeSidecar` (`_ensure_started`, `_rpc`, `_notify`, `_read_frame`, `call_tool`), `SIDECAR`, `register_sb3_tools(mcp)` |
| `scratch_unified/typed_proxy.py` | 51 typed `sb3_*` proxy functions (core + VM/debug/media + live-run + query) | `SB3_TOOL_DEFS` (see IDENTIFIERS.md §1 for the full list) |
| `scratch_unified/sidecar/` | vendored sidecar fork (`src/index.js runtime.js blocks.js jsonpatch.js bridge.js`; `FORK.md` provenance; `node_modules` symlinked to the upstream workspace) | upstream @ `cb1669aa`; feature delta in `FORK.md` |
| `scratch_unified/sb3_extra.py` | 9 native extras (no Node needed) | `sb3_git_unpack`, `sb3_git_pack`, `sb3_git_diff`, `sb3_studio_info`, `sb3_remixes`, `sb3_favorites`, `sb3_cloud_get_vars`, `sb3_cloud_set_var`, `sb3_cloud_logs` |
| `mcp.json` | client config snippet | server `scratch-unified`: `python3 -m scratch_unified`, `PYTHONPATH`, `SCRATCH_MCP_DATA_DIR`, timeout 600000 |
| `pyproject.toml` | packaging (`scratch-unified-mcp 1.0.0`) | deps `fastmcp`, `httpx`, `python-dotenv`, `scratchattach==3.0.0b3`; script `scratch-unified` |

## 3. Data flow

### 3.1 Startup

1. `main()` → `utils._restore()` reloads persisted session ids from
   `SCRATCH_MCP_DATA_DIR/sessions.json` into `SESSIONS`/`ACTIVE`.
2. `compat.apply()` patches scratchattach session-id decoding if broken.
3. `mcp.run(transport="stdio")` serves; `tools/list` reports 117 tools.

### 3.2 Native tool call (social/project/spy/extra)

Client → FastMCP dispatch → Python function → scratchattach / goboscript
CLI / in-process scratchpy core / zipfile+json → JSON string back.
Sessions are looked up via `maybe_ses()` / `active_ses()`; cloud/studio
writes fail with "call `social_connect_session` first" when absent.

### 3.3 Proxied tool call (51 × `sb3_*`)

Client → typed proxy fn → `SIDECAR.call_tool(name, args)` →
`_ensure_started()` spawns `node sidecar/src/index.js` once, sends
`initialize` + `notifications/initialized` → newline-delimited JSON-RPC
`tools/call` → Node runs scratch-vm / bridge logic → result text (or
`isError`) → returned verbatim (truncated at 50000 chars with a visible
marker). If Node or deps are missing, raises the `UNAVAILABLE` message
naming the unaffected families. `run_until` predicates execute inside
the sidecar on virtual time (the live-run tools), not as client polls.

## 4. Key design decisions

- **Single FastMCP app** (`vendor_uu/server.py`): vendored modules import
  `mcp` from there; `server.py` re-exports and adds families. Prevents
  split-brain registration.
- **Verbatim `social_*`/`project_*` names**: preserves upstream docs and
  prompts; collisions avoided by prefixing the other families.
- **Typed proxies, not `**kwargs`**: FastMCP constraint; each proxy has
  explicit params mirroring the Node `inputSchema`, with JSON-string
  params (`patch`, `props`, `items`, `keys`) parsed before forwarding.
- **Headless scratchpy**: real `tkinter` used when present, stubbed
  otherwise; one shared `MCPServer` rooted at
  `$TMPDIR/scratch-unified-spy/default.spy`.
- **Upstream dirs read-only**: `upstream-*` are reference + runtime dep
  (Node source, `.spy` core); never edited, so upstream updates are
  re-cloneable.

## 5. Failure modes

| Missing piece | Symptom | Unaffected |
|---|---|---|
| Node ≥ 18 / `pnpm install` not run | every `sb3_*` proxy raises `UNAVAILABLE` | `social_*`, `project_*`, `spy_*`, native extras |
| goboscript toolchain | `project_check_toolchain` reports not-ready; `project_build` fails | everything else |
| Scratch credentials | cloud/studio/publish tools raise "connect first" | reads that work logged-out, all local tools |
| TurboWarp + userscript | `sb3_screenshot`, `sb3_reload/run/stop_project` fail | headless `sb3_vm_*` still works |

