; Inno Setup 6 script for Energize Fitness (Windows).
; Build:  scripts\build_windows.ps1            (runs flutter build, then ISCC)
; Manual: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.0 installer\energize_fitness.iss
;
; Data lives in %APPDATA%\in.genus\... (per-user application support folder),
; NOT in the install folder, so upgrading or uninstalling never touches the
; gym's database. The uninstaller deliberately leaves that data in place.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Energize Fitness"
#define AppExe "gym_management_system.exe"
#define BuildDir "..\build\windows\x64\runner\Release"

[Setup]
; Fixed GUID: Windows uses it to recognise upgrades of the same product.
; Never change it after the first release.
AppId={{8F3C2B71-5D4A-4E0B-9C61-7A2E5B1D9F40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Genus
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\build\installer
OutputBaseFilename=EnergizeFitness-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#AppExe}
; Close the running app during an upgrade instead of failing on locked files.
CloseApplications=yes
RestartApplications=no
; Uncomment once a code-signing certificate is available (avoids SmartScreen warnings):
; SignTool=signtool $f

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Messages]
; Reassure the owner what uninstalling does to their data.
ConfirmUninstall=Remove {#AppName}? Your members and payment data are kept on this PC and will be available if you reinstall.
