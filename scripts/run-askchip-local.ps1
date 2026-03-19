$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$uiDir = Join-Path $root 'apps/askchip-ui'
$apiDir = Join-Path $root 'services/askchip-api'

$api = Start-Process -FilePath 'python' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $apiDir -PassThru
$ui = Start-Process -FilePath 'npm' -ArgumentList 'run','dev' -WorkingDirectory $uiDir -PassThru

Write-Host "AskChip API PID: $($api.Id)"
Write-Host "AskChip UI PID: $($ui.Id)"
Write-Host 'Press Enter to stop both processes.'
[void][System.Console]::ReadLine()

if (!$api.HasExited) { Stop-Process -Id $api.Id }
if (!$ui.HasExited) { Stop-Process -Id $ui.Id }
