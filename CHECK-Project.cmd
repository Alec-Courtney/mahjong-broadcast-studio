@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\check_project.py %*
set "check_result=%errorlevel%"
pause
exit /b %check_result%
