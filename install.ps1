# scratch-unified-mcp installer — native Windows PowerShell.
# Same flow as install.sh: check python3 (>=3.12) + node (>=18), pip install
# the package, npm install the sidecar deps, verify both.
#
# Usage (from a clone):
#   .\install.ps1 [-NoSidecar] [-NoVerify] [-Prefix DIR]
# One-liner:
#   git clone https://github.com/x3vu/scratch-unified-mcp.git; cd scratch-unified-mcp; .\install.ps1
param(
  [switch]$NoSidecar,
  [switch]$NoVerify,
  [string]$Prefix = ""
)

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "install.ps1: ERROR: $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host "install.ps1: $msg" }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
  Fail "cannot find pyproject.toml in $Root. Clone the repo first: git clone https://github.com/x3vu/scratch-unified-mcp.git"
}

$Py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
try { $PyVer = & $Py -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
catch { Fail "python not found. Install Python >= 3.12 (https://www.python.org/downloads/)." }
$Minor = [int]($PyVer.Split(".")[0]) * 100 + [int]($PyVer.Split(".")[1])
if ($Minor -lt 312) { Fail "python $PyVer too old - need >= 3.12." }
Info "python $PyVer ok"

$NodeOk = $false
try {
  $NodeMajor = [int]((node -p "process.versions.node.split('.')[0]" 2>$null))
  if ($NodeMajor -ge 18) { $NodeOk = $true; Info "node $(node --version) ok" }
} catch { }
if (-not $NodeOk) {
  Info "WARNING: node >= 18 not found (https://nodejs.org/). Continuing -"
  Info "  social_*, project_* and spy_* tools will work; sb3_* tools report unavailable until node is installed."
}

# --- Python package ---
$PipTarget = @()
if ($Prefix -ne "") { $PipTarget += "--target"; $PipTarget += $Prefix }
$Uv = Get-Command uv -ErrorAction SilentlyContinue
if ($Uv) {
  Info "installing python package with uv..."
  & uv pip install @PipTarget $env:PIP_FLAGS.Split(" ", [StringSplitOptions]::RemoveEmptyEntries) $Root
  if ($LASTEXITCODE -ne 0) { Fail "uv pip install failed." }
} else {
  Info "installing python package with pip..."
  & $Py -m pip install @PipTarget $Root
  if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
}

# --- Sidecar npm deps ---
if ($NoSidecar) {
  Info "skipping sidecar deps (-NoSidecar)."
} elseif (-not $NodeOk) {
  Info "skipping sidecar deps (no node) - re-run .\install.ps1 after installing node."
} else {
  $Sidecar = Join-Path $Root "scratch_unified\sidecar"
  if (-not (Test-Path (Join-Path $Sidecar "src"))) { Fail "sidecar src missing at $Sidecar\src." }
  Info "installing sidecar deps (npm)..."
  Push-Location $Sidecar
  try {
    if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund --legacy-peer-deps }
    else { npm install --no-audit --no-fund --legacy-peer-deps }
    if ($LASTEXITCODE -ne 0) { Fail "npm install failed in $Sidecar." }
  } finally { Pop-Location }
}

# --- Verify ---
if ($NoVerify) { Info "skipping verification (-NoVerify). Done."; exit 0 }

Info "verifying install..."
if (-not (Get-Command scratch-unified -ErrorAction SilentlyContinue)) {
  Write-Host "  WARNING: 'scratch-unified' not on PATH yet." -ForegroundColor Yellow
}
& $Py -m scratch_unified --help >$null 2>&1
if ($LASTEXITCODE -eq 0) { Info "python entrypoint ok." } else { Info "WARNING: 'python -m scratch_unified --help' failed." }
if ($NodeOk -and -not $NoSidecar) {
  & $Py (Join-Path $Root "scripts\smoke_sidecar.py")
  if ($LASTEXITCODE -eq 0) { Info "sidecar handshake ok." } else { Info "WARNING: sidecar handshake failed (see above)." }
}
Info "done. Next: add the MCP client config from README.md (Quickstart)."
