@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "demo-suite\library\library.json" (
  echo 完整演示数据尚未生成，请先运行生成脚本。
  pause
  exit /b 1
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo 请先运行 START-Windows.cmd 完成运行环境初始化，然后再运行本文件。
  pause
  exit /b 1
)
start "麻将完整演示库" ".venv\Scripts\pythonw.exe" run.py --data-dir "demo-suite\library"
exit /b 0
