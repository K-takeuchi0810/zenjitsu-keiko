$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat = Join-Path $scriptDir "run_weekly_validation_summary.bat"
$taskName = "keiba-trend-validation-weekly"

$batArg = '/d /c call "{0}" --no-pause' -f $bat
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $batArg -WorkingDirectory $scriptDir
# Run after the Monday 20:00 JV-Link sync so Sunday's confirmed results are in the DB.
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "20:30"
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
  -TaskName $taskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Description "Update latest trend validation pair and weekly signal summary." `
  -Force

Write-Host "Registered: $taskName"
Write-Host "Batch: $bat"
