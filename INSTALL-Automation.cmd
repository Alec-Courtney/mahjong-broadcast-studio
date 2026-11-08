@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo 识别组件需要 Python 3.11 或更高版本。当前环境请先升级后再安装。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-recognition.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\download_recognition_models.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import numpy,cv2,onnxruntime,mahjong,SpoutGL; print('自动识别、Spout 接收与计算组件已就绪')"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\configure_spout.py --enable-frame-count
if errorlevel 1 goto failed
echo 从 START-Windows.cmd 启动，在“赛事管理 → 赛事设置 → 画面与设备 → 牌面识别”选择来源并预览。
echo 与 OBS 共用四机位时选择 OBS Spout；来源滤镜须启用 Continuous filter broadcast。
echo OBS 插件版本与配置说明：docs\OBS_MULTI_CAMERA_DESIGN.md
pause
exit /b 0
:failed
echo 安装失败，请查看上方错误。赛事文件未改动。
pause
exit /b 1
