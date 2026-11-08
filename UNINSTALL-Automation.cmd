@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 本项目没有已安装的识别环境。
  pause
  exit /b 0
)
echo 请先关闭导播台，避免正在使用的识别组件无法卸载。
echo 仅卸载本项目的视觉识别与 Spout 接收组件，保留赛事、模型和手动导播功能。
pause
".venv\Scripts\python.exe" -m pip uninstall -y onnxruntime opencv-python-headless SpoutGL
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\configure_spout.py --restore-frame-count
if errorlevel 1 goto failed
echo 识别组件已卸载。重新打开导播台后，不再显示选手识别手牌状态。
echo 如需恢复，运行 INSTALL-Automation.cmd。OBS 插件由 OBS 自行管理。
pause
exit /b 0
:failed
echo 卸载未全部完成，请查看上方提示。赛事文件未改动。
pause
exit /b 1
