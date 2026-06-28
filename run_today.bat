@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set JVLINK_REALTIME_NO_DATA_SEC=3
set JV_ROOT=C:\Users\kizun\dev\keiba-yosou

rem Fetch today's win/place odds first so recommendations are not 0 due to missing odds.
rem If JV-Link is unavailable, skip odds and still run the trend report.
if not exist "%JV_ROOT%\.venv32\Scripts\python.exe" goto trend_report

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
echo === JV-Link today odds fetch ===
cd /d "%JV_ROOT%"
".venv32\Scripts\python.exe" -u -m scripts.fetch_odds --date %TODAY% --timeout-sec 3
if errorlevel 1 echo WARNING: odds fetch failed. Continuing without fresh odds.
cd /d "%~dp0"
echo.

:trend_report
echo === Trend report ===
py -3 collect_trends.py --date today --fallback-latest
echo.
echo Exit code: %errorlevel%
pause
