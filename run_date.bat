@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if "%~1"=="" (
  set /p TARGET_DATE=Date YYYYMMDD or YYYY-MM-DD:
) else (
  set TARGET_DATE=%~1
)

if "%TARGET_DATE%"=="" (
  echo ERROR: date is empty.
  goto :end
)

py -3 collect_trends.py --date "%TARGET_DATE%"

:end
echo.
echo Exit code: %errorlevel%
pause
