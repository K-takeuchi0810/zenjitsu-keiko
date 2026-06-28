@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
py -3 collect_trends.py --latest-completed
echo.
echo Exit code: %errorlevel%
pause
