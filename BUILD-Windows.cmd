@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller==6.22.3
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\build_windows_installer.py --bundle-only --output dist\windows
if errorlevel 1 goto failed
echo 生成完成，请查看 dist\windows\bundle\MahjongOverlayStudio。
pause
exit /b 0
:failed
echo 构建失败。若输出目录已存在，请先将旧目录改名，以保留已有数据。
pause
exit /b 1
