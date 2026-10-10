# Stops the daemon. The Edge test profile is left open (close its windows yourself so you do not lose your streams).
$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Stop-Process -Id $listener.OwningProcess -Force
    Write-Host "Daemon stopped."
} else {
    Write-Host "Daemon was not running."
}
