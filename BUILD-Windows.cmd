@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name MahjongOverlayStudio --paths src --hidden-import PySide6.QtQml ^
  --exclude-module cv2 --exclude-module numpy --exclude-module onnxruntime --exclude-module SpoutGL ^
  --add-data "LICENSE;." ^
  --add-data "docs\ORIGINAL_THIRD_PARTY_NOTICES.md;docs" ^
  --add-data "resources\engine.js;resources" ^
  --add-data "resources\NotoSansCJKjp-Regular.otf;resources" ^
  --add-data "resources\OFL-Noto.txt;resources" ^
  --add-data "resources\LICENSE-MahjongRepository.txt;resources" ^
  --add-data "resources\controls\*.svg;resources\controls" ^
  --add-data "resources\demo\*.json;resources\demo" ^
  --add-data "resources\demo\avatars\*.png;resources\demo\avatars" ^
  --add-data "resources\demo\portraits\mleague\*.png;resources\demo\portraits\mleague" ^
  --add-data "resources\demo_team_logos\*.png;resources\demo_team_logos" ^
  --add-data "resources\tiles\*.svg;resources\tiles" ^
  --add-data "resources\tiles\LICENSE-FluffyStuff.txt;resources\tiles" ^
  --add-data "resources\sticks\honba.png;resources\sticks" ^
  --add-data "resources\sticks\riichi.png;resources\sticks" ^
  --add-data "resources\sticks\LICENSE-riichinator.txt;resources\sticks" ^
  --add-data "resources\postmatch_audio\*_reveal.wav;resources\postmatch_audio" ^
  --add-data "resources\postmatch_audio\*_sweep.wav;resources\postmatch_audio" ^
  --add-data "resources\riichi_audio\riichi_declare.wav;resources\riichi_audio" run.py
if errorlevel 1 exit /b 1
if not exist "dist\MahjongOverlayStudio\demo-suite" (
  mkdir "dist\MahjongOverlayStudio\demo-suite"
  if errorlevel 1 exit /b 1
)
for %%D in (team-league-demo individual-league-demo individual-knockout-demo) do (
  copy /Y "demo-suite\%%D.json" "dist\MahjongOverlayStudio\demo-suite\%%D.json" >nul
  if errorlevel 1 exit /b 1
)
echo 生成完成：dist\MahjongOverlayStudio\MahjongOverlayStudio.exe
pause
