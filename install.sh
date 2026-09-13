#!/usr/bin/env sh
# scratch-unified-mcp installer — macOS / Linux / Windows (Git Bash, MSYS2, WSL).
# POSIX sh only (no bashisms): runs under sh, dash, bash, zsh, Git Bash.
#
# What it does:
#   1. Checks for python3 (>=3.12) and node (>=18).
#   2. Installs the Python package (pip or uv) so `scratch-unified` lands on PATH.
#   3. Installs the vendored sidecar's npm deps (npm ci if a lockfile exists,
#      else npm install --no-audit --no-fund).
#   4. Verifies: `scratch-unified --help` and the sidecar handshake.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/x3vu/scratch-unified-mcp/main/install.sh | sh
#   # or from a clone:
#   ./install.sh [--no-sidecar] [--no-verify] [--prefix DIR]
#
# Env knobs: PYTHON (python binary), PIP_FLAGS (extra pip args), NPM_FLAGS.

set -u

NO_SIDECAR=0
NO_VERIFY=0
PREFIX=""

for arg in "$@"; do
  case "$arg" in
    --no-sidecar) NO_SIDECAR=1 ;;
    --no-verify) NO_VERIFY=1 ;;
    --prefix=*) PREFIX="${arg#--prefix=}" ;;
    -h|--help)
      echo "Usage: install.sh [--no-sidecar] [--no-verify] [--prefix DIR]"
      echo "  --no-sidecar  skip sidecar npm deps (sb3_* tools report unavailable)"
      echo "  --no-verify   skip the post-install verification step"
      echo "  --prefix DIR  pass --target DIR to pip (portable install)"
      exit 0 ;;
    *) echo "install.sh: unknown flag '$arg' (try --help)" >&2; exit 1 ;;
  esac
done

# Resolve the repo root: the dir holding this script, even when piped via curl
# (in which case $0 is `sh` and we fall back to cwd).
ROOT=""
if [ -f "$0" ] && [ "$(basename "$0")" = "install.sh" ]; then
  ROOT="$(cd "$(dirname "$0")" && pwd)"
else
  ROOT="$(pwd)"
fi
if [ ! -f "$ROOT/pyproject.toml" ]; then
  echo "install.sh: cannot find pyproject.toml in $ROOT." >&2
  echo "  Clone the repo first: git clone https://github.com/x3vu/scratch-unified-mcp.git" >&2
  exit 1
fi

fail() { echo "install.sh: ERROR: $1" >&2; exit 1; }
info() { echo "install.sh: $1"; }

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || fail "python3 not found. Install Python >= 3.12 (https://www.python.org/downloads/)."

PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || fail "cannot run $PY."
case "$PYVER" in
  3.1[2-9]|3.[2-9][0-9]) info "python $PYVER ok" ;;
  *) fail "python $PYVER too old — need >= 3.12." ;;
esac

NODE_OK=0
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null)"
  case "$NODE_MAJOR" in
    ''|*[!0-9]*) info "node version unreadable — sidecar may not work" ;;
    *) if [ "$NODE_MAJOR" -ge 18 ]; then NODE_OK=1; info "node $(node --version) ok"; fi ;;
  esac
fi
if [ "$NODE_OK" -eq 0 ]; then
  info "WARNING: node >= 18 not found (https://nodejs.org/). Continuing —"
  info "  social_*, project_* and spy_* tools will work; sb3_* tools will report unavailable until node is installed."
fi

# --- Python package ---------------------------------------------------------
PIP_TARGET=""
if [ -n "$PREFIX" ]; then PIP_TARGET="--target $PREFIX"; fi

if command -v uv >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  info "installing python package with uv..."
  uv pip install $PIP_TARGET ${PIP_FLAGS:-} "$ROOT" || fail "uv pip install failed."
elif "$PY" -m pip --version >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  info "installing python package with pip..."
  "$PY" -m pip install $PIP_TARGET ${PIP_FLAGS:-} "$ROOT" || fail "pip install failed."
else
  fail "neither uv nor pip found for $PY."
fi

# --- Sidecar npm deps -------------------------------------------------------
if [ "$NO_SIDECAR" -eq 1 ]; then
  info "skipping sidecar deps (--no-sidecar)."
elif [ "$NODE_OK" -eq 0 ]; then
  info "skipping sidecar deps (no node) — run ./install.sh again after installing node."
else
  SIDECAR="$ROOT/scratch_unified/sidecar"
  if [ ! -d "$SIDECAR/src" ]; then fail "sidecar src missing at $SIDECAR/src."; fi
  info "installing sidecar deps (npm)..."
  # npm >= 11 vs git deps: pacote's nested `npm install --force` for the
  # scratch-vm git dep inherits `--allow-scripts` from ANY user-level .npmrc
  # (e.g. `allow-scripts=9router`) and dies with EALLOWSCRIPTS, because the
  # CLI flag is rejected in project-scoped installs. Neutralize the user
  # config for this one install (project .npmrc/.package.json policy still
  # applies). npm < 11 ignores the flag and installs normally.
  NPMRC_BACKUP=""
  if [ -f "$HOME/.npmrc" ] && grep -q "allow-scripts" "$HOME/.npmrc" 2>/dev/null; then
    NPMRC_BACKUP="$(mktemp -d)/.npmrc-backup"
    cp "$HOME/.npmrc" "$NPMRC_BACKUP"
    grep -v "allow-scripts" "$NPMRC_BACKUP" > "$HOME/.npmrc"
    info "(temporarily neutralized allow-scripts in ~/.npmrc; restoring after install)"
  fi
  if [ -f "$SIDECAR/package-lock.json" ]; then
    (cd "$SIDECAR" && npm ci --no-audit --no-fund --legacy-peer-deps ${NPM_FLAGS:-}) || SIDECAR_FAIL=1
  else
    (cd "$SIDECAR" && npm install --no-audit --no-fund --legacy-peer-deps ${NPM_FLAGS:-}) || SIDECAR_FAIL=1
  fi
  if [ -n "$NPMRC_BACKUP" ]; then
    cp "$NPMRC_BACKUP" "$HOME/.npmrc"
    info "(restored ~/.npmrc)"
  fi
  [ "${SIDECAR_FAIL:-0}" -eq 0 ] || fail "npm install failed in $SIDECAR."
fi

# --- Verify -----------------------------------------------------------------
if [ "$NO_VERIFY" -eq 1 ]; then
  info "skipping verification (--no-verify). Done."
  exit 0
fi

info "verifying install..."
command -v scratch-unified >/dev/null 2>&1 || {
  echo "  WARNING: 'scratch-unified' not on PATH yet (pip install dir not in PATH?)." >&2
  echo "  Try: $PY -m scratch_unified --help" >&2
}
"$PY" -m scratch_unified --help >/dev/null 2>&1 && info "python entrypoint ok." || info "WARNING: 'python3 -m scratch_unified --help' failed."

if [ "$NODE_OK" -eq 1 ] && [ "$NO_SIDECAR" -eq 0 ]; then
  "$PY" "$ROOT/scripts/smoke_sidecar.py" && info "sidecar handshake ok." || info "WARNING: sidecar handshake failed (see above)."
fi

info "done. Next: add the MCP client config from README.md (Quickstart)."
