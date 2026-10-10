# One-time setup of OmniSportsSwitch on a new machine. Safe to run again: each step is skipped when it is already done.
#
#   scripts\setup.ps1                 do everything
#   scripts\setup.ps1 -CheckOnly      only report what is missing, change nothing
#   scripts\setup.ps1 -SkipModels     do not download the models (PANNs checkpoint, EasyOCR models)
#   scripts\setup.ps1 -BuildLauncher  also build dist\OmniSportsSwitch.exe at the end
#
# If Windows says "running scripts is disabled":  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
param([switch]$CheckOnly, [switch]$SkipModels, [switch]$BuildLauncher)

$ErrorActionPreference = "Continue"   # native commands write warnings to stderr; every step checks its own exit code
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$panns = Join-Path $root "models\panns"
$problems = @()

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "   ok: $text" -ForegroundColor Green }
function Todo($text) { Write-Host "   TODO: $text" -ForegroundColor Yellow; $script:problems += $text }

# --- 1. Python ------------------------------------------------------------------------------------------------
Step "Python 3.10 - 3.13"
$pythonCmd = $null
foreach ($candidate in @(@("py", "-3.12"), @("py", "-3"), @("python"))) {
    try {
        $rest = @(); if ($candidate.Count -gt 1) { $rest = $candidate[1..($candidate.Count - 1)] }
        $version = & $candidate[0] @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -match '^3\.(1[0-3])$') { $pythonCmd = $candidate; Ok "Python $version ($($candidate -join ' '))"; break }
    } catch { }
}
if (-not $pythonCmd -and -not (Test-Path $venvPython)) { Todo "Install Python 3.12 from python.org (tick 'Add to PATH'), then run this script again."; }

# --- 2. GPU ---------------------------------------------------------------------------------------------------
Step "NVIDIA GPU"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { Ok (& nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | Select-Object -First 1) }
else { Todo "nvidia-smi not found: install an NVIDIA driver (the audio model and OCR run on the GPU)." }

# --- 3. Virtual environment -----------------------------------------------------------------------------------
Step "Virtual environment (.venv)"
if (Test-Path $venvPython) { Ok ".venv exists" }
elseif ($CheckOnly) { Todo "Create the environment (python -m venv .venv)." }
elseif ($pythonCmd) {
    $rest = @(); if ($pythonCmd.Count -gt 1) { $rest = $pythonCmd[1..($pythonCmd.Count - 1)] }
    & $pythonCmd[0] @rest -m venv .venv; Ok "created .venv"
}

# --- 4. PyTorch with CUDA (must come first) -------------------------------------------------------------------
Step "PyTorch with CUDA"
$cuda = $false
if (Test-Path $venvPython) {
    $answer = & $venvPython -c "import torch; print(torch.cuda.is_available())" 2>$null
    $cuda = ($answer -eq "True")
}
if ($cuda) { Ok "torch sees the GPU" }
elseif ($CheckOnly) { Todo "Install PyTorch from the CUDA index (pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128)." }
elseif (Test-Path $venvPython) {
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
    $answer = & $venvPython -c "import torch; print(torch.cuda.is_available())"
    if ($answer -eq "True") { Ok "torch sees the GPU" } else { Todo "PyTorch is installed but does not see the GPU: check the NVIDIA driver." }
}

# --- 5. The other packages ------------------------------------------------------------------------------------
Step "Python packages"
if (Test-Path $venvPython) {
    # panns_inference is not imported here: its package tries to download files with wget (not on Windows)
    & $venvPython -c "import websockets, numpy, mss, PIL, easyocr, torchlibrosa, importlib.metadata as m; m.version('panns-inference')" 2>$null
    if ($LASTEXITCODE -eq 0) { Ok "all packages are installed" }
    elseif ($CheckOnly) { Todo "Install the packages from requirements.txt." }
    else {
        # panns-inference is installed without its dependencies: they would replace the GPU PyTorch with a CPU one.
        $lines = Get-Content (Join-Path $root "requirements.txt") | Where-Object { $_ -notmatch '^\s*#' -and $_ -notmatch '^\s*$' -and $_ -notmatch 'panns-inference' }
        $temp = Join-Path $env:TEMP "oss-requirements.txt"
        Set-Content $temp ($lines | ForEach-Object { ($_ -split '#')[0].Trim() })
        & $venvPython -m pip install -r $temp
        & $venvPython -m pip install --no-deps panns-inference
        & $venvPython -c "import torch; assert torch.cuda.is_available(), 'the GPU build of PyTorch was replaced'"
        if ($LASTEXITCODE -eq 0) { Ok "packages installed, PyTorch still sees the GPU" } else { Todo "A package replaced the GPU PyTorch: run the PyTorch step again (delete .venv and rerun)." }
    }
}

# --- 6. Models ------------------------------------------------------------------------------------------------
Step "Models"
if ($SkipModels) { Write-Host "   skipped (-SkipModels)" }
else {
    New-Item -ItemType Directory -Force $panns | Out-Null
    $files = @(
        @{ Name = "Cnn14_mAP=0.431.pth"; Url = "https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth?download=1"; Size = 327428481 },
        @{ Name = "class_labels_indices.csv"; Url = "http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv"; Size = 14675 }
    )
    foreach ($f in $files) {
        $path = Join-Path $panns $f.Name
        if ((Test-Path $path) -and (Get-Item $path).Length -eq $f.Size) { Ok "$($f.Name)" }
        elseif ($CheckOnly) { Todo "Download $($f.Name) into models\panns." }
        else {
            Write-Host "   downloading $($f.Name) ($([math]::Round($f.Size / 1MB, 1)) MB)..."
            Invoke-WebRequest -Uri $f.Url -OutFile $path -UseBasicParsing
            if ((Get-Item $path).Length -eq $f.Size) { Ok "$($f.Name)" } else { Todo "$($f.Name) has an unexpected size: delete it and run again." }
        }
    }
    $ocrDir = Join-Path $root "models\easyocr"
    if ((Test-Path $ocrDir) -and (Get-ChildItem $ocrDir -ErrorAction SilentlyContinue)) { Ok "EasyOCR models" }
    elseif ($CheckOnly) { Todo "The EasyOCR models are downloaded on the first run (or run this script without -CheckOnly)." }
    elseif (Test-Path $venvPython) {
        Write-Host "   downloading the EasyOCR models (English, French)..."
        & $venvPython -c "from daemon.screen_ocr import ScreenReader; ScreenReader()"
        if ($LASTEXITCODE -eq 0) { Ok "EasyOCR models" } else { Todo "The EasyOCR models could not be downloaded." }
    }
}

# --- 7. Tests and launcher ------------------------------------------------------------------------------------
if (-not $CheckOnly -and (Test-Path $venvPython)) {
    Step "Tests"
    & $venvPython -m pytest daemon -q
    if ($LASTEXITCODE -eq 0) { Ok "tests pass (one expected failure is normal)" } else { Todo "Some tests failed: see the output above." }
    if ($BuildLauncher) { Step "Launcher"; & (Join-Path $PSScriptRoot "build_launcher.ps1") }
}

Write-Host ""
if ($problems.Count -eq 0) {
    Write-Host "Everything is in place. Start it with scripts\start_all.ps1 (or dist\OmniSportsSwitch.exe once built)." -ForegroundColor Green
} else {
    Write-Host "Still to do:" -ForegroundColor Yellow
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
}
Write-Host "Edge: turn hardware acceleration OFF (edge://settings/system), then load the extension (start_all.ps1 does it for you)."
