#ifndef ReleaseVersion
  #define ReleaseVersion "3.0.0-beta.6"
#endif
#define AppId "{5C807985-272E-4E9E-99D1-1CF815C51287}"

[Setup]
AppId={{#AppId}
AppName=MUR
AppVersion={#ReleaseVersion}
AppPublisher=MUR — Model Usage Reports
AppPublisherURL=https://github.com/oalanicolas/mur
DefaultDirName={localappdata}\Programs\MUR
DefaultGroupName=MUR
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\windows
OutputBaseFilename=MUR-{#ReleaseVersion}-windows-x64-setup
SetupIconFile=MUR.ico
UninstallDisplayIcon={app}\MUR.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "portuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\windows\app\MUR\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\installation\windows\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\MUR"; Filename: "{app}\MUR.exe"
Name: "{userdesktop}\MUR"; Filename: "{app}\MUR.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; Flags: unchecked

[Run]
Filename: "{app}\MUR.exe"; Description: "Abrir MUR"; Flags: nowait postinstall skipifsilent

[Code]
function HasWebView2(): Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKCU32, Key, 'pv', Version) or
             RegQueryStringValue(HKLM32, Key, 'pv', Version)) and (Version <> '') and (Version <> '0.0.0.0');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  if not HasWebView2() then begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := 'Não foi possível instalar o WebView2. Confira sua conexão e tente novamente.'
    else if not HasWebView2() then
      Result := 'O WebView2 não concluiu a instalação. Confira sua conexão e tente novamente.';
  end;
end;
