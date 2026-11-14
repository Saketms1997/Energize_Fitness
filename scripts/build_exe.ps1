<#
.SYNOPSIS
  Builds the Windows application folder: dist\GymManagement\GymManagement.exe (no Python needed to run it).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'Create the virtual environment first (see README.md, "Install and run").' }

& $py -m pip install --quiet -r requirements-dev.txt
if ($LASTEXITCODE) { throw 'Installing requirements failed.' }
& $py -m pytest -q -m "not db"          # fast safety net; full suite needs the database
if ($LASTEXITCODE) { throw 'Tests failed - not building.' }

& $py -m PyInstaller installer\GymManagement.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE) { throw 'PyInstaller failed.' }

$exe = Join-Path $root 'dist\GymManagement\GymManagement.exe'
if (-not (Test-Path $exe)) { throw 'Build finished but the executable is missing.' }
$size = [math]::Round((Get-ChildItem (Split-Path $exe) -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "Built $exe  ($size MB folder)"
