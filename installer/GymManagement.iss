; Inno Setup 6 script. Build with:  powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
; PostgreSQL is a separate prerequisite: the installer checks for it and tells the user how to install it.
#define AppName "Gym Management System"
#define AppVersion "1.0.0"
#define AppExe "GymManagement.exe"

[Setup]
AppId={{6F1B7C52-3E0A-4D9B-9A57-2C8E4A1D5B90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Energize Fitness
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist\installer
OutputBaseFilename=GymManagementSetup-{#AppVersion}
SetupIconFile=..\assets\icons\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; Re-running the installer upgrades in place and never touches %LOCALAPPDATA%\GymManagement (data, logs, backups).
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\GymManagement\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
function PostgresInstalled(): Boolean;
begin
  Result := RegKeyExists(HKLM64, 'SOFTWARE\PostgreSQL\Installations') or
            RegKeyExists(HKLM, 'SOFTWARE\PostgreSQL\Installations') or
            DirExists(ExpandConstant('{commonpf64}\PostgreSQL'));
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not PostgresInstalled() then
    Result := MsgBox('PostgreSQL does not appear to be installed on this PC.' + #13#10 + #13#10 +
      'The Gym Management System stores its data in PostgreSQL (with pgAdmin 4). Install PostgreSQL 15 or newer ' +
      'from postgresql.org first, then run the application: it will guide you through creating the database.' + #13#10 + #13#10 +
      'Continue installing the application anyway?', mbConfirmation, MB_YESNO) = IDYES;
end;
