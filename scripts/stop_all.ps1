# Stops the daemon. The Edge test profile is left open (close its windows yourself so you do not lose your streams),
# unless you ask for it:
#
#   scripts\stop_all.ps1          stop the daemon only
#   scripts\stop_all.ps1 -Edge    also close the Edge test profile (all its windows)
param([switch]$Edge)

$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Stop-Process -Id $listener.OwningProcess -Force
    Write-Host "Daemon stopped."
} else {
    Write-Host "Daemon was not running."
}

if ($Edge) {
    # only the Edge that uses the test profile folder; your normal Edge is not touched
    $testEdge = Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" |
        Where-Object { $_.CommandLine -like "*OmniSportsSwitch\edge-profile*" }
    if ($testEdge) {
        $testEdge | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Host "Edge test profile closed."
    } else {
        Write-Host "Edge test profile was not running."
    }
}
