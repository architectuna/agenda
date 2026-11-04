; Instalador de Agenda (Inno Setup 6)

#define AppName "Agenda"
#define AppVersion "1.3"

[Setup]
AppId={{6B1E7C2A-4F3D-4C1B-9A55-2E7D0A1F9C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Arquitectuna
AppPublisherURL=https://github.com/architectuna/agenda
AppSupportURL=https://github.com/architectuna/agenda/issues
VersionInfoVersion=1.3.0.0
VersionInfoDescription=Instalador de Agenda
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
; evita que se ejecuten varias copias del instalador a la vez
SetupMutex=AgendaSetupMutex,Global\AgendaSetupMutex
; detecta si Agenda está abierta antes de instalar o desinstalar
AppMutex=Agenda_GCal_Widget

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "inicio"; Description: "Abrir Agenda al iniciar Windows"
Name: "escritorio"; Description: "Crear acceso directo en el escritorio"; Flags: unchecked

[Files]
Source: "dist\Agenda\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Agenda.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\Agenda.exe"; Tasks: inicio
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\Agenda.exe"; Tasks: escritorio

[Run]
Filename: "{app}\Agenda.exe"; Description: "Abrir Agenda ahora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/IM Agenda.exe /F"; Flags: runhidden; RunOnceId: "CerrarAgenda"
