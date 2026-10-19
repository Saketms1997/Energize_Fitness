<#
  Builds the Windows release and, when Inno Setup is installed, the installer.

    powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-Version 1.0.0] [-SkipTests]

  Output: build\windows\x64\runner\Release\   (the app)
          build\installer\EnergizeFitness-Setup-<version>.exe
#>
param(
  [string]$Version = '',
  [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command flutter -ErrorAction SilentlyContinue)) {
  throw 'flutter is not on PATH. Add <flutter>\bin to PATH and retry.'
}
if (-not $Version) {
  $line = Select-String -Path pubspec.yaml -Pattern '^version:\s*([0-9]+\.[0-9]+\.[0-9]+)' | Select-Object -First 1
  $Version = $line.Matches[0].Groups[1].Value
}
Write-Host "Building Energize Fitness $Version"

flutter pub get
if ($LASTEXITCODE) { throw 'flutter pub get failed' }
dart run build_runner build --delete-conflicting-outputs
if ($LASTEXITCODE) { throw 'build_runner failed' }
flutter analyze
if ($LASTEXITCODE) { throw 'flutter analyze reported problems' }
if (-not $SkipTests) {
  flutter test
  if ($LASTEXITCODE) { throw 'tests failed - not building a release' }
}

# Cloud project values are public by design (protected by row-level security).
# Pass them at build time if you want them pre-filled in Settings:
#   -Define SUPABASE_URL=...,SUPABASE_ANON_KEY=...   (see .env.example)
$defines = @()
if ($env:SUPABASE_URL)      { $defines += "--dart-define=SUPABASE_URL=$env:SUPABASE_URL" }
if ($env:SUPABASE_ANON_KEY) { $defines += "--dart-define=SUPABASE_ANON_KEY=$env:SUPABASE_ANON_KEY" }

flutter build windows --release --build-name=$Version @defines
if ($LASTEXITCODE) { throw 'flutter build windows failed' }

$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
  Write-Warning 'Inno Setup 6 not found - installer NOT built. Install it from https://jrsoftware.org/isinfo.php and rerun.'
  Write-Host 'The app itself is ready in build\windows\x64\runner\Release\'
  exit 0
}
& $iscc "/DAppVersion=$Version" installer\energize_fitness.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed' }
Write-Host "Installer: build\installer\EnergizeFitness-Setup-$Version.exe"
