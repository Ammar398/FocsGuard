# FocusGuard installer for Windows.
#
# Started by "Install FocusGuard.cmd". Installs for the current Windows user only;
# no administrator rights are needed. Safe to run again: it updates in place.
#
#   1. find Python 3.11, or install it with winget
#   2. copy FocusGuard to %LOCALAPPDATA%\FocusGuard\app
#   3. create its own Python environment and install the packages it needs
#   4. put the AI models in place
#   5. remember which FocusGuard server this computer uses (server.txt)
#   6. start the FocusGuard Helper now and at every Windows sign-in
#
# Set FOCUSGUARD_INSTALL_DIR to install somewhere else (e.g. another drive).

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$source = $PSScriptRoot

function Say($text) { Write-Host "`n>> $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host "`nFocusGuard could not be installed: $text" -ForegroundColor Red
    exit 1
}
function Check($what) {
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE). Check your internet connection and run the installer again." }
}

$server = (Get-Content -Path (Join-Path $source 'server.txt') -TotalCount 1).Trim()
if (-not $server) { Fail 'server.txt is missing. Download the installer again from your FocusGuard website.' }

$app = $env:FOCUSGUARD_INSTALL_DIR
if (-not $app) { $app = Join-Path $env:LOCALAPPDATA 'FocusGuard\app' }

Write-Host 'FocusGuard setup' -ForegroundColor Yellow
Write-Host "Server:      $server"
Write-Host "Install to:  $app"

# --- 1. Python 3.11 ---------------------------------------------------------------
function Find-Python {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:ProgramFiles 'Python311\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $found = & py.exe -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
    }
    return $null
}

Say 'Looking for Python 3.11'
$python = Find-Python
if (-not $python) {
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        Say 'Installing Python 3.11 (for your user only)'
        & winget.exe install --exact --id Python.Python.3.11 --scope user --silent --accept-package-agreements --accept-source-agreements
        $python = Find-Python
    }
}
if (-not $python) {
    Start-Process 'https://www.python.org/downloads/release/python-3119/'
    Fail 'Python 3.11 is needed. Install it from the page that just opened (tick "Add python.exe to PATH"), then run this installer again.'
}
Write-Host "Using $python"

# --- 2. files ----------------------------------------------------------------------
Say 'Copying FocusGuard'
New-Item -ItemType Directory -Force -Path $app | Out-Null
foreach ($folder in @('shared', 'desktop_agent', 'requirements')) {
    $target = Join-Path $app $folder
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    Copy-Item -Recurse -Force (Join-Path $source $folder) $target
}

# --- 3. Python environment ----------------------------------------------------------
$venv = Join-Path $app '.venv'
$py = Join-Path $venv 'Scripts\python.exe'
$pyw = Join-Path $venv 'Scripts\pythonw.exe'
if (-not (Test-Path $py)) {
    Say 'Creating the Python environment'
    & $python -m venv $venv
    Check 'Creating the Python environment'
}

Say 'Installing packages (the first time takes several minutes)'
& $py -m pip install --disable-pip-version-check --upgrade pip --quiet
Check 'Updating pip'
& $py -m pip install --disable-pip-version-check -r (Join-Path $app 'requirements\agent.txt')
Check 'Installing packages'

# Make FocusGuard importable from this environment, wherever it is run from.
$site = (& $py -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
$paths = (Join-Path $app 'shared') + "`r`n" + (Join-Path $app 'desktop_agent') + "`r`n"
[System.IO.File]::WriteAllText((Join-Path $site 'focusguard.pth'), $paths)

# --- 4. models -----------------------------------------------------------------------
Say 'Preparing the AI models'
$models = Join-Path $env:LOCALAPPDATA 'FocusGuard\models'
New-Item -ItemType Directory -Force -Path $models | Out-Null
Copy-Item -Force (Join-Path $source 'models\yolov8n.onnx') (Join-Path $models 'yolov8n.onnx')
& $py -m focusguard_agent.hardware.model_store
Check 'Downloading the face model'

# --- 5 and 6. server, Helper ---------------------------------------------------------------
Say 'Starting the FocusGuard Helper'
# Stop a Helper that is already running, so the new version takes over.
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'focusguard_agent\.helper' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

Push-Location $app
try {
    & $py -m focusguard_agent.helper --server $server --install-startup --setup-only
    Check 'Saving the server address'
    Start-Process -FilePath $pyw -ArgumentList '-m', 'focusguard_agent.helper' -WorkingDirectory $app -WindowStyle Hidden
}
finally {
    Pop-Location
}

Write-Host "`nFocusGuard is ready." -ForegroundColor Green
Write-Host 'Go back to the FocusGuard website and click "Connect this computer".'
Start-Process $server
