# Starts the OmniSportsSwitch daemon (WebSocket server on ws://127.0.0.1:8765). Stop it with Ctrl+C.
Set-Location (Join-Path $PSScriptRoot "..")
& .\.venv\Scripts\python.exe -m daemon.server
