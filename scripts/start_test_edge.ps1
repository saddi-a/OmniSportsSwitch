# Starts the separate "test" Edge profile used for development: the extension from ../extension is
# preloaded and the debug port 9222 is open. Your normal Edge profile is not touched.
$profile = Join-Path $env:LOCALAPPDATA "OmniSportsSwitch\edge-profile"
$extension = Join-Path $PSScriptRoot "..\extension" | Resolve-Path

Start-Process "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=`"$profile`"",
    "--load-extension=`"$extension`"",
    "--disable-features=DisableLoadExtensionCommandLineSwitch",
    "--no-first-run",
    "--restore-last-session"
)
