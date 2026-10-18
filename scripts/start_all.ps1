# Starts everything: the daemon (in its own window) and the test Edge profile with the extension loaded.
# Anything that is already running is left alone, so it is safe to run twice.
#
#   scripts\start_all.ps1            start the daemon and Edge, wait until the daemon is ready
#   scripts\start_all.ps1 -NoEdge    start only the daemon (for example when you use your own Edge profile)
#
# The daemon window is minimized: closing it stops the daemon. dist\OmniSportsSwitch.exe does the same with no window.
param([switch]$NoEdge)

$root = Split-Path $PSScriptRoot -Parent

function Test-Port($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Test-Path (Join-Path $root ".venv\Scripts\python.exe"))) {
    Write-Host "The Python environment is missing. Run scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

if (Test-Port 8765) {
    Write-Host "Daemon: already running (port 8765)."
} else {
    Write-Host "Daemon: starting in a new window (the models take about 15 s to load)..."
    Start-Process powershell -WindowStyle Minimized -WorkingDirectory $root -ArgumentList "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start_daemon.ps1")
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Port 8765) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (Test-Port 8765) {
        Write-Host "Daemon: ready." -ForegroundColor Green
    } else {
        Write-Host "Daemon: did not start within 90 s. Open its window (taskbar) or read logs\daemon.log." -ForegroundColor Yellow
    }
}

if ($NoEdge) {
    Write-Host "Edge: not started (-NoEdge). Load the extension yourself: edge://extensions, Developer mode, Load unpacked, the extension folder."
} elseif (Test-Port 9222) {
    Write-Host "Edge test profile: already running (debug port 9222)."
} else {
    Write-Host "Edge test profile: starting with the extension loaded..."
    & (Join-Path $PSScriptRoot "start_test_edge.ps1")
}

# Optional screenshot recorder (kept outside the repo, next to it; a second copy exits at once by its lock file).
$capture = Join-Path (Split-Path $root -Parent) "OmniSportsSwitch-capture\capture_loop.py"
if (Test-Path $capture) {
    Start-Process (Join-Path $root ".venv\Scripts\python.exe") -WindowStyle Hidden -WorkingDirectory (Split-Path $capture -Parent) -ArgumentList $capture
    Write-Host "Recorder: started (OmniSportsSwitch-capture, one frame per tracked tile every 30 s)."
}

Write-Host ""
Write-Host "Next: open your streams, then in each window press Ctrl+Shift+Y (or the extension icon -> Track this tab)."
Write-Host "Window names are restored automatically if the windows sit where they did before. Freeze Main with Ctrl+Shift+P."
Write-Host "Logs: $root\logs\events.log (important events) and $root\logs\daemon.log (everything)."
