; ---------------------------------------------------------------------------
; TikVault — installeur Windows (Inno Setup 6)
;
; Prérequis (sur Windows) :
;   1. dist\TikVault.exe déjà fabriqué :
;        uv run python scripts/fetch_ffmpeg.py        (une fois, embarque FFmpeg)
;        uv sync --extra build
;        uv run pyinstaller build\tikvault.spec --noconfirm
;   2. Inno Setup 6 :  winget install JRSoftware.InnoSetup
;
; Compilation :
;   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
;
; Résultat : dist\TikVault-2.0.0-installeur.exe
;   - assistant d'installation en français ;
;   - installation par utilisateur (aucun droit administrateur requis) ;
;   - raccourcis menu Démarrer + Bureau (optionnel) ;
;   - désinstallation propre depuis « Applications installées ».
; ---------------------------------------------------------------------------

#define MyAppName "TikVault"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "TikVault"
#define MyAppExeName "TikVault.exe"

[Setup]
; AppId : NE JAMAIS CHANGER entre deux versions (sinon Windows voit deux logiciels).
AppId={{C7A01E2F-4B8D-4F5E-9A3C-2D7E6F1B9A04}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Installation par utilisateur : pas de droits admin, pas d'écran UAC.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
; L'exécutable PyInstaller est déjà compressé : lzma2 reste utile pour l'enrobage.
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-installeur
; Fermer TikVault s'il tourne pendant une mise à jour, le relancer après.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Rien d'autre : les téléchargements de l'utilisateur et ses réglages
; (%LOCALAPPDATA%\TikVault) ne sont PAS supprimés — ce sont ses données.
Type: filesandordirs; Name: "{app}"
