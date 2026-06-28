@echo off
chcp 65001 >nul
setlocal

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set JV_ROOT=C:\Users\kizun\dev\keiba-yosou
set NO_PAUSE=0
set SKIP_IF_NO_RACE_TODAY=0
set SKIP_TRAINING=0
set SCRIPT_DIR=%~dp0
set LOG_DIR=%SCRIPT_DIR%reports\logs
set LOCK_DIR=%LOG_DIR%\sync_jvlink_then_collect.lock
set FETCH_TIMEOUT_SEC=180
set TRAINING_TIMEOUT_SEC=300

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-pause" set NO_PAUSE=1
if /i "%~1"=="--skip-if-no-race-today" set SKIP_IF_NO_RACE_TODAY=1
if /i "%~1"=="--skip-training" set SKIP_TRAINING=1
if /i "%~1"=="--fetch-timeout-sec" (
  set FETCH_TIMEOUT_SEC=%~2
  shift
)
if /i "%~1"=="--training-timeout-sec" (
  set TRAINING_TIMEOUT_SEC=%~2
  shift
)
shift
goto parse_args
:args_done

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set LOG_TS=%%i
set LOG_FILE=%LOG_DIR%\%LOG_TS%_sync_jvlink_then_collect.log

call :acquire_lock > "%LOG_FILE%" 2>&1
if errorlevel 1 (
  set EXIT_CODE=0
  >> "%LOG_FILE%" echo.
  >> "%LOG_FILE%" echo Exit code: %EXIT_CODE%
  type "%LOG_FILE%"
  echo.
  echo Log: "%LOG_FILE%"
  if "%NO_PAUSE%"=="0" pause
  exit /b %EXIT_CODE%
)

call :run >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%errorlevel%
call :release_lock >> "%LOG_FILE%" 2>&1
>> "%LOG_FILE%" echo.
>> "%LOG_FILE%" echo Exit code: %EXIT_CODE%
type "%LOG_FILE%"
echo.
echo Log: "%LOG_FILE%"
if "%NO_PAUSE%"=="0" pause
exit /b %EXIT_CODE%

:acquire_lock
powershell -NoProfile -Command "$p = '%LOCK_DIR%'; if (Test-Path -LiteralPath $p) { $age = (Get-Date) - (Get-Item -LiteralPath $p).LastWriteTime; if ($age.TotalHours -gt 6) { Remove-Item -LiteralPath $p -Recurse -Force } }"
mkdir "%LOCK_DIR%" 2>nul
if errorlevel 1 (
  echo Another sync_jvlink_then_collect run is active. Skipping this run.
  echo Lock: "%LOCK_DIR%"
  exit /b 1
)
echo Lock acquired: "%LOCK_DIR%"
exit /b 0

:release_lock
if exist "%LOCK_DIR%" rmdir "%LOCK_DIR%" 2>nul
echo Lock released: "%LOCK_DIR%"
exit /b 0

:run
echo Log file: "%LOG_FILE%"
echo Started: %DATE% %TIME%

for /f %%i in ('powershell -NoProfile -Command "[DateTimeOffset]::Now.ToUnixTimeSeconds() - 60"') do set FETCH_STARTED=%%i
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd')"') do set RESULT_DATE=%%i
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(1).ToString('yyyyMMdd')"') do set NEXT_DATE=%%i
echo Result date rule: today "%RESULT_DATE%"
echo Next recommendation date: "%NEXT_DATE%"

if not exist "%JV_ROOT%\.venv32\Scripts\python.exe" (
  echo ERROR: 32bit Python venv was not found.
  echo Path: "%JV_ROOT%\.venv32\Scripts\python.exe"
  goto :run_end
)

echo === JV-Link RACE fetch ===
cd /d "%JV_ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '.venv32\Scripts\python.exe' -ArgumentList '-m','scripts.fetch_full','--dataspecs','RACE' -NoNewWindow -PassThru; if (-not $p.WaitForExit([int]$env:FETCH_TIMEOUT_SEC * 1000)) { try { $p.Kill() } catch {}; Write-Error ('RACE fetch timed out after ' + $env:FETCH_TIMEOUT_SEC + ' sec'); exit 124 }; exit $p.ExitCode"
if errorlevel 1 goto :run_end

echo.
echo === SQLite ingest ===
".venv32\Scripts\python.exe" -c "from jvlink_client.ingest import ingest_all; print(ingest_all(dataspecs=['RACE'], modified_since=float('%FETCH_STARTED%')))"
if errorlevel 1 goto :run_end

if not "%SKIP_IF_NO_RACE_TODAY%"=="1" goto after_race_day_guard
echo.
echo === Race-day guard ===
cd /d "%SCRIPT_DIR%"
py -3 race_day_guard.py --date today
if errorlevel 3 exit /b %errorlevel%
if errorlevel 2 (
  echo No race program for today. Skipping training, realtime result, odds, and trend report.
  exit /b 0
)
if errorlevel 1 exit /b %errorlevel%
cd /d "%JV_ROOT%"
:after_race_day_guard

echo.
echo === JV-Link realtime result fetch ===
echo dataspec: DIFN
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '.venv32\Scripts\python.exe' -ArgumentList '-m','scripts.fetch_full','--dataspecs','DIFN' -NoNewWindow -PassThru; if (-not $p.WaitForExit([int]$env:FETCH_TIMEOUT_SEC * 1000)) { try { $p.Kill() } catch {}; Write-Error ('DIFN fetch timed out after ' + $env:FETCH_TIMEOUT_SEC + ' sec'); exit 124 }; exit $p.ExitCode"
if errorlevel 1 goto :run_end

echo.
echo === DIFN ingest ===
".venv32\Scripts\python.exe" -c "from jvlink_client.ingest import ingest_all; print(ingest_all(dataspecs=['DIFN'], modified_since=float('%FETCH_STARTED%')))"
if errorlevel 1 goto :run_end

echo.
echo === JV-Link result fetch ===
echo dataspec: 0B12
echo result_date: %RESULT_DATE%
".venv32\Scripts\python.exe" -u -m scripts.fetch_results --date %RESULT_DATE% --timeout-sec 3
if errorlevel 1 goto :run_end

echo.
echo === JV-Link training fetch ===
if "%SKIP_TRAINING%"=="1" (
  echo Training fetch skipped by --skip-training.
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '.venv32\Scripts\python.exe' -ArgumentList '-m','scripts.fetch_full','--dataspecs','SLOP','WOOD' -NoNewWindow -PassThru; if (-not $p.WaitForExit([int]$env:TRAINING_TIMEOUT_SEC * 1000)) { try { $p.Kill() } catch {}; Write-Error ('training fetch timed out after ' + $env:TRAINING_TIMEOUT_SEC + ' sec'); exit 124 }; exit $p.ExitCode"
  if errorlevel 1 goto :run_end
)

echo.
echo === Training ingest ===
".venv32\Scripts\python.exe" -c "from jvlink_client.ingest import ingest_all; print(ingest_all(dataspecs=['SLOP','WOOD'], modified_since=float('%FETCH_STARTED%')))"
if errorlevel 1 goto :run_end

echo.
echo === JV-Link data mining fetch ===
echo dataspec: 0B13 0B17
echo target_date: %NEXT_DATE%
".venv32\Scripts\python.exe" -u -m scripts.fetch_mining --date %NEXT_DATE% --timeout-sec 3
if errorlevel 1 goto :run_end

echo.
echo === JV-Link next race odds fetch ===
set JVLINK_REALTIME_NO_DATA_SEC=3
".venv32\Scripts\python.exe" -u -m scripts.fetch_odds --date %NEXT_DATE% --timeout-sec 3
if errorlevel 1 goto :run_end

echo.
echo === Trend report ===
cd /d "%SCRIPT_DIR%"
py -3 collect_trends.py --date %RESULT_DATE% --next-date %NEXT_DATE% --intraday-date %NEXT_DATE%
set TREND_EXIT=%errorlevel%
if not "%TREND_EXIT%"=="0" goto :run_end

:run_end
exit /b %errorlevel%
