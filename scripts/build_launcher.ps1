# Builds dist\OmniSportsSwitch.exe: a small launcher (tray icon) that starts the daemon and the test Edge profile.
# The exe does not contain the daemon; it starts this project's .venv, so keep it inside the project folder.
# Needs the build tools once:  .venv\Scripts\python.exe -m pip install -r requirements-build.txt
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

& $python -c "import PyInstaller, pystray" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the build tools (pyinstaller, pystray)..."
    & $python -m pip install -r requirements-build.txt
}

# the icon of the exe
$icon = Join-Path $root "build\launcher.ico"
New-Item -ItemType Directory -Force (Split-Path $icon) | Out-Null
& $python -c "import sys; sys.path.insert(0, 'launcher'); import launcher; launcher.make_icon_image().save(r'$icon', sizes=[(16,16),(32,32),(48,48),(64,64)])"

& $python -m PyInstaller --noconfirm --onefile --noconsole --name OmniSportsSwitch --icon $icon `
    --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build\pyinstaller") --specpath (Join-Path $root "build") `
    --collect-submodules pystray launcher\launcher.py
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Built: $root\dist\OmniSportsSwitch.exe  (double-click it; check with: dist\OmniSportsSwitch.exe --status)"
}
