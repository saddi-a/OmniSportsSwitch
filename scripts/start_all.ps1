# Starts everything: the daemon (in its own window) and the test Edge profile with the extension loaded.
# Anything that is already running is left alone, so it is safe to run twice.
$root = Split-Path $PSScriptRoot -Parent

function Test-Port($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (Test-Port 8765) {
    Write-Host "Daemon: already running (port 8765)."
} else {
    Write-Host "Daemon: starting in a new window (first start takes about 15 s while the models load)..."
    Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-NoProfile", "-File", (Join-Path $PSScriptRoot "start_daemon.ps1")
}

if (Test-Port 9222) {
    Write-Host "Edge test profile: already running (debug port 9222)."
} else {
    Write-Host "Edge test profile: starting with the extension loaded..."
    & (Join-Path $PSScriptRoot "start_test_edge.ps1")
}

Write-Host ""
Write-Host "Next: open your streams in the Edge test windows, then on each stream tab click the extension icon -> Track this tab."
Write-Host "Window roles are restored automatically if the windows sit where they did before. Freeze MAIN with Ctrl+Shift+P."
Write-Host "Logs: $root\logs\events.log (important events) and $root\logs\daemon.log (everything)."
