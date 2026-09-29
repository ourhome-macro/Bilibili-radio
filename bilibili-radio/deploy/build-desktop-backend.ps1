param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$backendDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'py-radio'
$venvDir = Join-Path $backendDir '.venv-desktop'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the desktop Python environment' }
}

Push-Location $backendDir
try {
    & $venvPython -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install backend dependencies' }
    & $venvPython -m pip install --disable-pip-version-check pyinstaller==6.11.1
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install PyInstaller' }
    & $venvPython -m PyInstaller --clean --noconfirm bilibili_radio_backend.spec
    if ($LASTEXITCODE -ne 0) { throw 'Failed to package the desktop backend' }
    if (-not (Test-Path -LiteralPath 'dist\bilibili-radio-backend.exe' -PathType Leaf)) {
        throw 'The backend executable was not produced'
    }
} finally {
    Pop-Location
}
