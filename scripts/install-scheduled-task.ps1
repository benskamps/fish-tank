param(
    [string]$TaskName = "FishTankTick",
    [int]$IntervalMinutes = 15,
    [string]$PythonW = ""
)

# Prefer the interpreter handed to us (the venv where `tank` lives); only fall
# back to PATH lookup if none was provided.
if ($PythonW -and (Test-Path $PythonW)) {
    $pythonw = $PythonW
} else {
    $pythonw = (Get-Command pythonw.exe).Source
}

$ErrorActionPreference = "Stop"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m tank tick"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
           -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
# Restart-on-failure is RestartCount + RestartInterval (there is no
# -RestartOnFailure switch). ExecutionTimeLimit caps a single tick.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -RestartCount 2 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 60)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                       -Settings $settings -Force | Out-Null

# Don't claim success unless the task is really there.
if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    Write-Error "Task registration reported success but $TaskName is not present."
    exit 1
}
Write-Host "Registered scheduled task: $TaskName (every $IntervalMinutes minutes)"
