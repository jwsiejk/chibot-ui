Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message) { Write-Host "[ERROR] $Message" -ForegroundColor Red }

function Import-DotEnvFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    $parts = $line -split '=', 2
    if ($parts.Count -ne 2) { return }
    [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
  }
}

function Get-Env([string]$Name, [string]$Default) {
  $value = [System.Environment]::GetEnvironmentVariable($Name, 'Process')
  if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
  return $value
}

function Test-Endpoint([string]$Url) {
  try {
    $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 5
    return @{ ok = $true; status = $response.StatusCode; note = "HTTP $($response.StatusCode)" }
  } catch {
    return @{ ok = $false; status = $null; note = $_.Exception.Message }
  }
}

$repoRoot = Get-Location
$envLocal = Join-Path $repoRoot '.env.local'
$envExample = Join-Path $repoRoot '.env.example'
if (Test-Path -LiteralPath $envLocal) {
  Write-Info 'Loading .env.local'
  Import-DotEnvFile $envLocal
} elseif (Test-Path -LiteralPath $envExample) {
  Write-Warn '.env.local not found; loading .env.example defaults for checks.'
  Import-DotEnvFile $envExample
} else {
  Write-Warn 'No .env.local or .env.example found; using built-in defaults.'
}

$ollamaBaseUrl = Get-Env 'OLLAMA_BASE_URL' 'http://127.0.0.1:11434'
$ollamaModel = Get-Env 'OLLAMA_MODEL' 'gemma3:4b'
$kokoroBaseUrl = Get-Env 'KOKORO_TTS_BASE_URL' 'http://127.0.0.1:8880'
$whisperBaseUrl = Get-Env 'FASTER_WHISPER_BASE_URL' 'http://127.0.0.1:8890'
$appUrl = Get-Env 'APP_BASE_URL' 'http://127.0.0.1:4173/chappy'

Write-Info "Checking Ollama runtime: $ollamaBaseUrl"
$ollamaRuntime = Test-Endpoint "$ollamaBaseUrl/api/tags"
$ollamaModelOk = $false
$ollamaModelNote = 'Ollama runtime unreachable.'
if ($ollamaRuntime.ok) {
  try {
    $tags = Invoke-RestMethod -Uri "$ollamaBaseUrl/api/tags" -Method Get -TimeoutSec 5
    $models = @($tags.models | ForEach-Object { $_.name })
    $ollamaModelOk = $models -contains $ollamaModel
    $ollamaModelNote = if ($ollamaModelOk) { "Model '$ollamaModel' listed." } else { "Model '$ollamaModel' not listed." }
  } catch {
    $ollamaModelNote = "Failed to parse /api/tags response: $($_.Exception.Message)"
  }
}

Write-Info "Checking Kokoro TTS runtime: $kokoroBaseUrl"
$kokoroHealth = Test-Endpoint "$kokoroBaseUrl/health"
if (-not $kokoroHealth.ok) { $kokoroHealth = Test-Endpoint "$kokoroBaseUrl/v1/health" }
$kokoroNote = $kokoroHealth.note
if (-not $kokoroHealth.ok) {
  $kokoroNote = "Health endpoints unavailable. AskChappy runtime can still use existing synthetic readiness fallback; this script does not synthesize audio."
}

Write-Info "Checking faster-whisper STT runtime: $whisperBaseUrl"
$whisperHealth = Test-Endpoint "$whisperBaseUrl/health"

$rows = @(
  [pscustomobject]@{ Component = 'Ollama runtime'; Status = $(if ($ollamaRuntime.ok) { 'OK' } else { 'FAIL' }); Details = $ollamaRuntime.note },
  [pscustomobject]@{ Component = 'Ollama model'; Status = $(if ($ollamaModelOk) { 'OK' } else { 'FAIL' }); Details = $ollamaModelNote },
  [pscustomobject]@{ Component = 'Kokoro TTS'; Status = $(if ($kokoroHealth.ok) { 'OK' } else { 'WARN' }); Details = $kokoroNote },
  [pscustomobject]@{ Component = 'faster-whisper STT'; Status = $(if ($whisperHealth.ok) { 'OK' } else { 'FAIL' }); Details = $whisperHealth.note },
  [pscustomobject]@{ Component = 'AskChappy app URL'; Status = 'INFO'; Details = $appUrl }
)

Write-Host ''
$rows | Format-Table -AutoSize
Write-Host ''
Write-Info 'Manual GPU check guidance: run nvidia-smi -l 1 in a separate terminal when generating local workload.'

$requiredFailure = -not $ollamaRuntime.ok -or -not $ollamaModelOk -or -not $whisperHealth.ok
if ($requiredFailure) {
  Write-Err 'Required local runtime checks failed. Resolve errors above and rerun.'
  exit 1
}

Write-Info 'Required local runtime checks passed.'
exit 0
