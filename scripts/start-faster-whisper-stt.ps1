Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Import-DotEnvFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    $parts = $line -split '=', 2
    if ($parts.Count -eq 2) { [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process') }
  }
}
function Get-Env([string]$Name, [string]$Default) {
  $value = [System.Environment]::GetEnvironmentVariable($Name, 'Process')
  if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
  return $value
}

$root = Get-Location
if (Test-Path .env.local) { Import-DotEnvFile (Join-Path $root '.env.local') } elseif (Test-Path .env.example) { Import-DotEnvFile (Join-Path $root '.env.example') }

$baseUrl = Get-Env 'FASTER_WHISPER_BASE_URL' 'http://127.0.0.1:8890'
$model = Get-Env 'FASTER_WHISPER_MODEL' 'base.en'
$language = Get-Env 'FASTER_WHISPER_LANGUAGE' 'en'
$runCommand = Get-Env 'FASTER_WHISPER_RUN_COMMAND' ''

if (-not $baseUrl.StartsWith('http://127.0.0.1') -and -not $baseUrl.StartsWith('http://localhost')) { throw "FASTER_WHISPER_BASE_URL must be local-only. Current: $baseUrl" }
Write-Host "[INFO] faster-whisper config: base=$baseUrl model=$model language=$language"

if ([string]::IsNullOrWhiteSpace($runCommand)) {
  throw 'FASTER_WHISPER_RUN_COMMAND is not configured. Set it in .env.local to your local faster-whisper server command. Example: python path\\to\\faster_whisper_server.py --host 127.0.0.1 --port 8890'
}

Write-Host '[INFO] Starting faster-whisper runner command...'
Write-Host "[INFO] $runCommand"
Invoke-Expression $runCommand
