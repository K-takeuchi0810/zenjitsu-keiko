@echo off
chcp 65001 >nul
setlocal

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set NO_PAUSE=0
set SCRIPT_DIR=%~dp0
set LOG_DIR=%SCRIPT_DIR%reports\logs

if /i "%~1"=="--no-pause" set NO_PAUSE=1

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set LOG_TS=%%i
if "%TARGET_YEAR%"=="" for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy"') do set TARGET_YEAR=%%i
set LOG_FILE=%LOG_DIR%\%LOG_TS%_weekly_validation_summary.log

call :run > "%LOG_FILE%" 2>&1
set EXIT_CODE=%errorlevel%
>> "%LOG_FILE%" echo.
>> "%LOG_FILE%" echo Exit code: %EXIT_CODE%
type "%LOG_FILE%"
echo.
echo Log: "%LOG_FILE%"
if "%NO_PAUSE%"=="0" pause
exit /b %EXIT_CODE%

:run
echo Log file: "%LOG_FILE%"
echo Started: %DATE% %TIME%
echo Target year: %TARGET_YEAR%

cd /d "%SCRIPT_DIR%"

echo === Trend validation pending pairs ===
py -3 compare_previous_trends.py --pending-pairs --target-year %TARGET_YEAR%
if errorlevel 1 goto :run_end

echo.
echo === Trend validation summary ===
py -3 summarize_trend_validation.py --target-year %TARGET_YEAR%
if errorlevel 1 goto :run_end

echo.
echo === Trend validation pending pairs (current season) ===
py -3 compare_previous_trends.py --pending-pairs
if errorlevel 1 goto :run_end

echo.
echo === Trend validation summary (current season) ===
py -3 summarize_trend_validation.py
if errorlevel 1 goto :run_end

echo.
echo === Recommendation result validation ===
py -3 evaluate_recommendations.py
if errorlevel 1 goto :run_end

:run_end
exit /b %errorlevel%
