# Starts "durian supervise" at boot as SYSTEM using a scheduled task (works on stock Windows, no NSSM).
# Run from an elevated PowerShell:  .\install-windows-service.ps1 [-Dir D:\DurianDB] [-Python C:\Python312\python.exe]
# Ceilings: this is a scheduled task, not a Windows service (sc.exe create needs a service-aware binary, and
# python.exe is not one), so it has no Services-panel entry; stop it with Stop-ScheduledTask -TaskName DurianDB.
# Set DURIAN_* variables machine-wide (setx /M) because SYSTEM does not read your user environment.
param([string]$Dir = (Resolve-Path "$PSScriptRoot\..\..").Path, [string]$Python = (Get-Command python).Source)
$action = New-ScheduledTaskAction -Execute $Python -Argument "-m durian supervise" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName DurianDB -Action $action -Trigger $trigger -Settings $settings `
  -User SYSTEM -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName DurianDB
Write-Host "DurianDB scheduled task installed and started."
