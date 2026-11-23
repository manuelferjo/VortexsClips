; Instalador de Vortexs Clips (Inno Setup 6). Lo compila GitHub Actions en cada versión.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId=VortexsClips-manuelferjo
AppName=Vortexs Clips
AppVersion={#AppVersion}
AppPublisher=Vortexs
DefaultDirName={localappdata}\Programs\Vortexs Clips
DefaultGroupName=Vortexs Clips
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=VortexsClips-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\VortexsClips.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "dist\VortexsClips\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "ffmpeg\bin\*"; DestDir: "{app}\ffmpeg\bin"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\Vortexs Clips"; Filename: "{app}\VortexsClips.exe"
Name: "{autodesktop}\Vortexs Clips"; Filename: "{app}\VortexsClips.exe"; Tasks: desktopicon

[Run]
; se abre al terminar, también tras una actualización silenciosa
Filename: "{app}\VortexsClips.exe"; Description: "Abrir Vortexs Clips"; Flags: nowait postinstall
