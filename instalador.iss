; Script do Inno Setup para o Painel de Pendencias (Soto Company).
;
; Como gerar o instalador:
;   1) Empacotar o app com PyInstaller (gera a pasta "dist\Painel de Pendencias"):
;      python -m PyInstaller --noconfirm --windowed --name "Painel de Pendencias" ^
;          --icon "assets/icon.ico" --add-data "assets/icon.ico;assets" app.py
;   2) Compilar este script com o Inno Setup (ISCC.exe instalador.iss), ou abrir
;      no Inno Setup Compiler e clicar em "Compile".
;
; O instalador NAO inclui data\, config.json, credentials.json nem token_drive.json
; (sao gerados/configurados a cada instalacao). Instala numa pasta por usuario
; (AppData\Local), sem precisar de permissao de administrador, ja que o proprio
; programa grava banco de dados e configuracoes ao lado do executavel.

#define MyAppName "Painel de Pendencias"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Soto Company"
#define MyAppExeName "Painel de Pendencias.exe"

[Setup]
AppId={{3A9DEA4B-DCDB-44F5-B311-2F81848BE185}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=PainelDePendencias_Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na área de trabalho"; GroupDescription: "Atalhos adicionais:"

[Files]
Source: "dist\Painel de Pendencias\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Inicia automaticamente com o Windows (atalho na pasta Inicializar do usuario).
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent
