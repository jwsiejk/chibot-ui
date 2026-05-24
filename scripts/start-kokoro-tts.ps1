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

$baseUrl = Get-Env 'KOKORO_TTS_BASE_URL' 'http://127.0.0.1:8880'
$voice = Get-Env 'KOKORO_TTS_VOICE' 'af_sarah'
$format = Get-Env 'KOKORO_TTS_FORMAT' 'wav'
$assetDir = Get-Env 'KOKORO_TTS_ASSET_DIR' 'C:\AskChipAssets\kokoro'
$runCommand = Get-Env 'KOKORO_TTS_RUN_COMMAND' ''

if (-not $baseUrl.StartsWith('http://127.0.0.1') -and -not $baseUrl.StartsWith('http://localhost')) { throw "KOKORO_TTS_BASE_URL must be local-only. Current: $baseUrl" }
Write-Host "[INFO] Kokoro config: base=$baseUrl voice=$voice format=$format"
Write-Host "[INFO] Kokoro asset dir: $assetDir"

$onnxPath = Join-Path $assetDir 'kokoro-v1.0.onnx'
$voicesPath = Join-Path $assetDir 'voices-v1.0.bin'
if (Test-Path -LiteralPath $assetDir) {
  if (-not (Test-Path -LiteralPath $onnxPath)) { Write-Host "[WARN] Missing expected model file: $onnxPath" -ForegroundColor Yellow }
  if (-not (Test-Path -LiteralPath $voicesPath)) { Write-Host "[WARN] Missing expected voices file: $voicesPath" -ForegroundColor Yellow }
} else {
  Write-Host "[WARN] KOKORO_TTS_ASSET_DIR does not exist: $assetDir" -ForegroundColor Yellow
}

if ([string]::IsNullOrWhiteSpace($runCommand)) {
  throw 'KOKORO_TTS_RUN_COMMAND is not configured. Set it in .env.local to your local Kokoro runner command. Example: python path\to\kokoro_server.py --host 127.0.0.1 --port 8880'
}

Write-Host "[INFO] Starting Kokoro runner command..."
Write-Host "[INFO] $runCommand"
Invoke-Expression $runCommand
