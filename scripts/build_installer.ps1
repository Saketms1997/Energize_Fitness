<#
.SYNOPSIS
  Builds the executable, then wraps it in a Windows installer with Inno Setup 6.
  Output: dist\installer\GymManagementSetup-<version>.exe
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$iscc = @("$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php and run this again.' }

& "$PSScriptRoot\build_exe.ps1"
& $iscc "installer\GymManagement.iss"
if ($LASTEXITCODE) { throw 'Inno Setup failed.' }
Get-ChildItem dist\installer\*.exe | ForEach-Object { Write-Host "Installer: $($_.FullName)" }
