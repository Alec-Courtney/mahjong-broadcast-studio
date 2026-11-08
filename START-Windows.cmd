@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto checkdeps
py -3 --version >nul 2>&1
if not errorlevel 1 (
  set "TASK_PYTHON=py -3"
) else (
  python --version >nul 2>&1
  if not errorlevel 1 set "TASK_PYTHON=python"
)
if not defined TASK_PYTHON (
  echo 请安装 Python 3.10 或更高版本，再重新运行本文件。
  pause
  exit /b 1
)
%TASK_PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo 需要 Python 3.10 或更高版本。
  pause
  exit /b 1
)
%TASK_PYTHON% -m venv .venv
if errorlevel 1 goto failed
:checkdeps
".venv\Scripts\python.exe" -c "from PySide6.QtWidgets import QApplication; from PySide6.QtQml import QJSEngine; import mahjong" >nul 2>&1
if not errorlevel 1 goto launch
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:launch
start "麻将转播控制台" ".venv\Scripts\pythonw.exe" run.py %*
exit /b 0
:failed
echo 初始化失败，请查看上方错误后重新运行。
pause
exit /b 1
