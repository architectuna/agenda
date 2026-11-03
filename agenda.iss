; Instalador de Agenda (Inno Setup 6)

#define AppName "Agenda"
#define AppVersion "1.2"

[Setup]
AppId={{6B1E7C2A-4F3D-4C1B-9A55-2E7D0A1F9C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Arquitectuna
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=instalador
OutputBaseFilename=Agenda-Setup
SetupIconFile=agenda.ico
UninstallDisplayIcon={app}\Agenda.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "inicio"; Description: "Abrir Agenda al iniciar Windows"
Name: "escritorio"; Description: "Crear acceso directo en el escritorio"; Flags: unchecked

[Files]
Source: "dist\Agenda.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Agenda.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\Agenda.exe"; Tasks: inicio
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\Agenda.exe"; Tasks: escritorio

[Run]
Filename: "{app}\Agenda.exe"; Description: "Abrir Agenda ahora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/IM Agenda.exe /F"; Flags: runhidden; RunOnceId: "CerrarAgenda"
