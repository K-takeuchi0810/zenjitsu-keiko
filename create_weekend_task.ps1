$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat = Join-Path $scriptDir "sync_jvlink_then_collect.bat"
$taskName = "keiba-trend-collect-raceday"
$legacyTaskName = "keiba-trend-collect-weekend"

$batArg = '/d /c call "{0}" --no-pause --skip-if-no-race-today' -f $bat
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $batArg -WorkingDirectory $scriptDir
$triggerDaily = New-ScheduledTaskTrigger -Daily -At "20:00"
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

if ($legacyTaskName -ne $taskName) {
  Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false -ErrorAction SilentlyContinue
}

Register-ScheduledTask `
  -TaskName $taskName `
  -Action $action `
  -Trigger $triggerDaily `
  -Principal $principal `
  -Description "Fetch JRA-VAN data daily at 20:00, skip non-race days, and generate trend report." `
  -Force

Write-Host "Registered: $taskName"
Write-Host "Batch: $bat"
Write-Host "Schedule: Daily 20:00"
