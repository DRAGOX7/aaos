<#
.SYNOPSIS
Start AAOS: the backend and the dashboard, each in its own window, then open the dashboard.

.DESCRIPTION
The backend runs from the data folder, exactly as in the README's manual steps. Each server
gets its own window so its log stays visible; closing a window stops that server. A server
that is already answering is left alone rather than started twice.

Both windows inherit this terminal's environment, so a cloud model chosen with
AAOS_MODEL_PROVIDER (and GEMINI_API_KEY for Gemini) must be set in the terminal that runs
this script. Nothing is written to a file. Started by double-click, AAOS uses the local
model, and LM Studio has to be running for it; the script warns when it is not.

.PARAMETER DataDir
The AAOS data folder. Defaults to AAOS_DATA_DIR, else an `aaos-data` folder next to the
repository.

.PARAMETER NoBrowser
Start the servers without opening the browser.

.EXAMPLE
.\scripts\start-aaos.ps1
.\scripts\start-aaos.ps1 -DataDir D:\aaos-data
#>
[CmdletBinding()]
param(
    [string]$DataDir,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$BackendPort = 8000
$FrontendPort = 3000
$DashboardUrl = "http://localhost:$FrontendPort"
$LmStudioUrl = 'http://127.0.0.1:1234/v1/models'

function Write-Step([string]$Message) { Write-Host "AAOS: $Message" }

function Stop-WithError([string]$Message) {
    Write-Host "AAOS: $Message" -ForegroundColor Red
    exit 1
}

function Test-Port([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        return $client.ConnectAsync('127.0.0.1', $Port).Wait(300) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Test-Url([string]$Url) {
    try {
        $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return $true
    } catch {
        return $false
    }
}

function Wait-Url([string]$Url, [string]$Name, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $Url) { return $true }
        Start-Sleep -Seconds 1
    }
    Write-Host "AAOS: $Name did not answer within $Seconds seconds; check its window." -ForegroundColor Yellow
    return $false
}

# Quote a path for a single-quoted PowerShell string inside the child command.
function ConvertTo-Literal([string]$Text) { "'" + $Text.Replace("'", "''") + "'" }

function Start-Server([string]$Title, [string]$Command) {
    # The same PowerShell that runs this script (Windows PowerShell or PowerShell 7).
    $shell = (Get-Process -Id $PID).Path
    $full = "`$Host.UI.RawUI.WindowTitle = $(ConvertTo-Literal $Title); $Command"
    Start-Process -FilePath $shell -ArgumentList @('-NoLogo', '-NoProfile', '-NoExit', '-Command', $full) | Out-Null
}

# --- check the pieces exist --------------------------------------------------------------

if (-not $DataDir) {
    $DataDir = if ($env:AAOS_DATA_DIR) { $env:AAOS_DATA_DIR } else { Join-Path (Split-Path -Parent $Repo) 'aaos-data' }
}
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Frontend = Join-Path $Repo 'frontend'

if (-not (Test-Path -LiteralPath $DataDir -PathType Container)) {
    Stop-WithError "data folder not found: $DataDir. Pass -DataDir or set AAOS_DATA_DIR (see the README's Getting started)."
}
$DataDir = (Resolve-Path -LiteralPath $DataDir).Path
if (-not (Test-Path -LiteralPath $Python)) {
    Stop-WithError "Python environment not found at $Python. Install AAOS first (README, Getting started step 1)."
}
if (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) {
    Stop-WithError "dashboard packages are missing. Run 'npm install' in $Frontend first."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Stop-WithError "npm was not found. Install Node.js, then try again."
}

$provider = if ($env:AAOS_MODEL_PROVIDER) { $env:AAOS_MODEL_PROVIDER } else { 'lm_studio' }
if ($provider -eq 'lm_studio' -and -not (Test-Url $LmStudioUrl)) {
    Write-Host 'AAOS: LM Studio is not answering on 127.0.0.1:1234. The dashboard will open, but questions need it: start LM Studio and its local server.' -ForegroundColor Yellow
} elseif ($provider -ne 'lm_studio') {
    Write-Step "model provider from this terminal: $provider"
}

# --- start what is not already running -----------------------------------------------------

$started = @()
if (Test-Port $BackendPort) {
    Write-Step "backend already running on port $BackendPort; leaving it."
} else {
    Write-Step "starting the backend from $DataDir"
    $started += '"AAOS backend"'
    Start-Server 'AAOS backend' (
        "Set-Location -LiteralPath $(ConvertTo-Literal $DataDir); `$env:PYTHONUTF8 = '1'; " +
        "& $(ConvertTo-Literal $Python) -m uvicorn aaos.main:app --host 127.0.0.1 --port $BackendPort"
    )
}

if (Test-Port $FrontendPort) {
    Write-Step "dashboard already running on port $FrontendPort; leaving it."
} else {
    Write-Step 'starting the dashboard'
    $started += '"AAOS dashboard"'
    Start-Server 'AAOS dashboard' "Set-Location -LiteralPath $(ConvertTo-Literal $Frontend); npm run dev"
}

# --- wait, then open ---------------------------------------------------------------------------

Write-Step 'waiting for the backend and the dashboard to answer...'
$backendUp = Wait-Url "http://127.0.0.1:$BackendPort/health" 'The backend' 120
$dashboardUp = Wait-Url $DashboardUrl 'The dashboard' 180

if ($backendUp -and $dashboardUp) {
    Write-Step "ready at $DashboardUrl"
    if (-not $NoBrowser) { Start-Process $DashboardUrl }
    if ($started) { Write-Step "to stop it, close the $($started -join ' and ') window(s)." }
    exit 0
}
exit 1
