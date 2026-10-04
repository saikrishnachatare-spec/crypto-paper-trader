$ErrorActionPreference = "Stop"

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "This distribution requires 64-bit Windows."
}

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    throw "Python 3.12 x64 is required. Install it from https://www.python.org/downloads/windows/ with the Python Launcher, then run this setup again."
}

$pythonCheck = & $pythonLauncher.Source -3.12 -c "import struct, sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+';'+str(struct.calcsize('P')*8))" 2>&1
if ($LASTEXITCODE -ne 0 -or $pythonCheck -notmatch "^3\.12;64$") {
    throw "Python 3.12 x64 was not found through the Python Launcher. Install Python 3.12 x64 from https://www.python.org/downloads/windows/ and retry."
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $pythonLauncher.Source -3.12 -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the local Laya environment." }
}

Write-Output "Installing the pinned Laya CPU runtime. This needs internet access and can take several minutes."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip in the local environment." }
& $venvPython -m pip install -r (Join-Path $root "requirements-laya.txt")
if ($LASTEXITCODE -ne 0) { throw "Laya runtime installation failed. Check the network connection and Python 3.12 x64 installation, then rerun setup." }

& $venvPython -c "import laya, torch; print('Laya:', getattr(laya, '__version__', 'installed')); print('PyTorch:', torch.__version__)"
if ($LASTEXITCODE -ne 0) { throw "The Laya runtime verification failed." }

Write-Output "Setup complete. Try: .\CryptoPaperTrader.exe --dry-run"
