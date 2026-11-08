@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "demo-suite\library\library.json" (
  echo 此入口需要本机已有的完整演示库。
  echo 首次使用请双击 START-Windows.cmd，在「赛事管理」中打开内置演示。
  echo 其他演示可在同一页点「导入完整赛事 JSON」，选择 demo-suite 文件夹中的文件。
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
