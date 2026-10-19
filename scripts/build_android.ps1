<#
  Builds a signed Android release.

    powershell -ExecutionPolicy Bypass -File scripts\build_android.ps1 [-Bundle]

  One-time setup (see docs\SETUP_GUIDE.md, "Android signing"):
    1. Create a keystore:  keytool -genkey -v -keystore %USERPROFILE%\energize-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias energize
    2. Create android\key.properties (NOT committed; listed in .gitignore):
         storePassword=...
         keyPassword=...
         keyAlias=energize
         storeFile=C:/Users/<you>/energize-release.jks
  BACK UP THE KEYSTORE AND PASSWORDS. Without them you can never publish an
  update to the same app.
#>
param([switch]$Bundle)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path android\key.properties)) {
  throw 'android\key.properties is missing - refusing to build a release signed with the debug key. See the header of this script.'
}
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter analyze
if ($LASTEXITCODE) { throw 'flutter analyze reported problems' }
flutter test
if ($LASTEXITCODE) { throw 'tests failed - not building a release' }

if ($Bundle) { flutter build appbundle --release } else { flutter build apk --release }
if ($LASTEXITCODE) { throw 'flutter build failed' }
