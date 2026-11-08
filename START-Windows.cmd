@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "run.py" goto incomplete
if not exist "requirements.txt" goto incomplete
if exist ".venv\Scripts\pythonw.exe" goto checkdeps
set "TASK_PYTHON="
py -3 --version >nul 2>&1
if not errorlevel 1 (
  set "TASK_PYTHON=py -3"
) else (
  python --version >nul 2>&1
  if not errorlevel 1 set "TASK_PYTHON=python"
)
if not defined TASK_PYTHON (
  echo 请安装 64 位 Python 3.13，再重新运行本文件。
  pause
  exit /b 1
)
%TASK_PYTHON% -c "import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,16) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
  echo 需要 64 位 Python 3.10 至 3.15。推荐安装 Python 3.13。
  pause
  exit /b 1
)
%TASK_PYTHON% -m venv .venv
if errorlevel 1 goto failed
:checkdeps
".venv\Scripts\python.exe" -c "from PySide6.QtWidgets import QApplication; from PySide6.QtQml import QJSEngine; import mahjong" >nul 2>&1
if not errorlevel 1 goto launch
echo 正在安装基础组件，请保持联网。安装完成后会自动打开导播台。
".venv\Scripts\python.exe" -m pip install --log ".venv\install.log" -r requirements.txt
if errorlevel 1 goto failed
:launch
start "麻将转播控制台" ".venv\Scripts\pythonw.exe" run.py %*
if errorlevel 1 goto failed
exit /b 0
:incomplete
echo 找不到项目文件。请解压完整项目，再双击其中的 START-Windows.cmd。
pause
exit /b 1
:failed
echo 启动失败。请检查网络，并确认项目已解压到本机可写的文件夹。
echo 如果提示找不到安装文件或路径过长，请把整个项目移到较短的路径，例如 C:\Mahjong。
echo 移动后可将项目里的 .venv 文件夹移到项目外，再双击本文件重试。
echo 如需排查，请保留上方的错误信息。
if exist ".venv\install.log" echo 安装日志位于 .venv\install.log。
pause
exit /b 1
