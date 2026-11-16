; Keep this AppId identical to the 2026-11-14 installer.
[Setup]
AppId={{958755AD-379A-4C80-A9F5-CB1F3DE54FA9}
AppName=Mahjong Broadcast Studio
AppVersion={#AppVersion}
VersionInfoVersion={#VersionInfoVersion}
AppPublisher=Mahjong Broadcast Studio
DefaultDirName={localappdata}\Programs\MahjongBroadcastStudio
DefaultGroupName=Mahjong Broadcast Studio
UsePreviousAppDir=yes
UsePreviousTasks=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
LicenseFile={#ProjectRoot}\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=MahjongOverlayStudio-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MahjongOverlayStudio.exe
SetupIconFile={#ProjectRoot}\resources\app_icon\mahjong_broadcast.ico
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
; This bundle contains program files only. No data or imported media is shipped.
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Mahjong Broadcast Studio"; Filename: "{app}\MahjongOverlayStudio.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Mahjong Broadcast Studio"; Filename: "{app}\MahjongOverlayStudio.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\MahjongOverlayStudio.exe"; Description: "Launch Mahjong Broadcast Studio"; Flags: nowait postinstall skipifsilent

[Code]
procedure BackupTree(const Source, Target: String);
var
  Entry: TFindRec;
begin
  if not DirExists(Source) then Exit;
  if not ForceDirectories(Target) then RaiseException('Unable to create update backup: ' + Target);
  if FindFirst(Source + '\*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          if Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
            BackupTree(Source + '\' + Entry.Name, Target + '\' + Entry.Name)
          else if ExtractFileExt(Entry.Name) <> '.lock' then
            if not FileCopy(Source + '\' + Entry.Name, Target + '\' + Entry.Name, False) then
              RaiseException('Unable to back up existing data: ' + Source + '\' + Entry.Name);
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir, BackupDir: String;
  ConversionExit: Integer;
begin
  if CurStep = ssInstall then begin
    AppDir := ExpandConstant('{app}');
    if DirExists(AppDir + '\data') then begin
      BackupDir := AppDir + '\update-backups\before-{#AppVersion}-' + GetDateTimeString('yyyymmdd-hhnnss', '-', ':');
      Log('Preserving existing event library in ' + BackupDir);
      BackupTree(AppDir + '\data', BackupDir + '\data');
      BackupTree(AppDir + '\assets\imported', BackupDir + '\assets\imported');
    end;
  end;
  if CurStep = ssPostInstall then begin
    AppDir := ExpandConstant('{app}');
    if not Exec(AppDir + '\MahjongOverlayStudio.exe', '--migrate-backups --root "' + AppDir + '"', AppDir,
                SW_HIDE, ewWaitUntilTerminated, ConversionExit) then
      RaiseException('Unable to run backup migration. Existing JSON files were preserved.');
    if ConversionExit <> 0 then
      RaiseException('Some legacy backups could not be converted. Existing JSON files were preserved. See data\backup-migration-{#AppVersion}.json.');
  end;
end;
