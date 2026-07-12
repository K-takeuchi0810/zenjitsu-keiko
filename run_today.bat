@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem Odds/popularity are no longer used by the tendency report or recommendations,
rem so no JV-Link odds fetch here. This runs a report for the latest completed day.
echo === Trend report ===
py -3 collect_trends.py --date today --fallback-latest
echo.
echo Exit code: %errorlevel%
pause
