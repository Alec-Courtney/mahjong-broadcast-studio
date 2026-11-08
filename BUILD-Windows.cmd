@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "BUNDLE_DIR=dist\MahjongOverlayStudio"
if exist "%BUNDLE_DIR%" goto existing_bundle
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller==6.22.3
if errorlevel 1 goto failed
if exist "%BUNDLE_DIR%" goto existing_bundle
rem Keep DLLs from unrelated applications out of the package.
set "PATH=%SystemRoot%\System32;%SystemRoot%"
set "PYTHONPATH="
set "PYTHONHOME="
".venv\Scripts\python.exe" -m PyInstaller --clean --windowed --onedir ^
  --name MahjongOverlayStudio --paths src --additional-hooks-dir scripts\pyinstaller\hooks ^
  --exclude-module cv2 --exclude-module numpy --exclude-module onnxruntime --exclude-module SpoutGL ^
  --add-data "LICENSE;." ^
  --add-data "docs\ORIGINAL_THIRD_PARTY_NOTICES.md;docs" ^
  --add-data "resources\engine.js;resources" ^
  --add-data "resources\ASSET_SOURCES.json;resources" ^
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
if errorlevel 1 goto failed
mkdir "%BUNDLE_DIR%\demo-suite" || goto failed
mkdir "%BUNDLE_DIR%\licenses" || goto failed
for %%D in (team-league-demo individual-league-demo individual-knockout-demo) do (
  copy /Y "demo-suite\%%D.json" "%BUNDLE_DIR%\demo-suite\%%D.json" >nul || goto failed
)
copy /Y "demo-suite\PHOTO_NOTICE.md" "%BUNDLE_DIR%\demo-suite\PHOTO_NOTICE.md" >nul || goto failed
copy /Y "resources\demo\roster.json" "%BUNDLE_DIR%\demo-suite\roster.json" >nul || goto failed
copy /Y "docs\START_HERE_WINDOWS.txt" "%BUNDLE_DIR%\START-HERE.txt" >nul || goto failed
copy /Y "LICENSE" "%BUNDLE_DIR%\LICENSE" >nul || goto failed
copy /Y "mahjong_camera_switch.lua" "%BUNDLE_DIR%\mahjong_camera_switch.lua" >nul || goto failed
for %%L in (WINDOWS_RUNTIME_NOTICES.md QT-THIRD-PARTY-NOTICES.txt LGPL-3.0-only.txt GPL-3.0-only.txt) do (
  copy /Y "resources\licenses\%%L" "%BUNDLE_DIR%\licenses\%%L" >nul || goto failed
)
".venv\Scripts\python.exe" -c "from pathlib import Path; from shutil import copyfile; import sys; copyfile(Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.argv[1]))" "%BUNDLE_DIR%\licenses\PYTHON-LICENSE.txt"
if errorlevel 1 goto failed
echo 生成完成。请查看 %BUNDLE_DIR%\START-HERE.txt。
pause
exit /b 0
:existing_bundle
echo 构建目录 %BUNDLE_DIR% 已存在，可能包含已保存赛事或导入素材。
echo 请先把原目录改名或移到其他位置，再重新构建。
pause
exit /b 1
:failed
echo 构建失败，请查看上方错误。
pause
exit /b 1
