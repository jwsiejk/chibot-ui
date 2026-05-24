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

function Test-Health([string]$Url) {
  try { Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 5 | Out-Null; return $true } catch { return $false }
}

$root = Get-Location
$envLocal = Join-Path $root '.env.local'
if (-not (Test-Path -LiteralPath $envLocal)) {
  Write-Host '[ERROR] .env.local is missing. Copy .env.example to .env.local and configure local runner commands first.' -ForegroundColor Red
  exit 1
}
Import-DotEnvFile $envLocal

$ollamaBase = [System.Environment]::GetEnvironmentVariable('OLLAMA_BASE_URL', 'Process')
if ([string]::IsNullOrWhiteSpace($ollamaBase)) { $ollamaBase = 'http://127.0.0.1:11434' }
$kokoroBase = [System.Environment]::GetEnvironmentVariable('KOKORO_TTS_BASE_URL', 'Process')
if ([string]::IsNullOrWhiteSpace($kokoroBase)) { $kokoroBase = 'http://127.0.0.1:8880' }
$sttBase = [System.Environment]::GetEnvironmentVariable('FASTER_WHISPER_BASE_URL', 'Process')
if ([string]::IsNullOrWhiteSpace($sttBase)) { $sttBase = 'http://127.0.0.1:8890' }

$missing = @()

Write-Host "[INFO] Step 1: checking Ollama at $ollamaBase"
if (-not (Test-Health "$ollamaBase/api/tags")) {
  Write-Host '[ERROR] Ollama is not reachable. Start Ollama and verify OLLAMA_BASE_URL.' -ForegroundColor Red
  $missing += 'ollama'
}

Write-Host "[INFO] Step 2: checking Kokoro at $kokoroBase"
$kokoroReady = (Test-Health "$kokoroBase/health") -or (Test-Health "$kokoroBase/v1/health")
if (-not $kokoroReady) {
  Write-Host '[ERROR] Kokoro is not reachable.' -ForegroundColor Red
  $missing += 'kokoro'
}

Write-Host "[INFO] Step 3: checking faster-whisper at $sttBase"
if (-not (Test-Health "$sttBase/health")) {
  Write-Host '[ERROR] faster-whisper is not reachable.' -ForegroundColor Red
  $missing += 'faster-whisper'
}

if ($missing.Count -gt 0) {
  Write-Host ''
  Write-Host '[ERROR] Required local runtime services are missing/unreachable. Start services in separate PowerShell windows and rerun:' -ForegroundColor Red
  if ($missing -contains 'ollama') {
    Write-Host '  - Start Ollama using your normal local startup process.'
  }
  if ($missing -contains 'kokoro') {
    Write-Host '  - .\scripts\start-kokoro-tts.ps1'
  }
  if ($missing -contains 'faster-whisper') {
    Write-Host '  - .\scripts\start-faster-whisper-stt.ps1'
  }
  Write-Host '  - .\scripts\check-local-runtime.ps1'
  Write-Host 'Then rerun .\scripts\start-local-runtime.ps1 to launch AskChappy.'
  exit 1
}

Write-Host '[INFO] Step 4: all required services reachable; starting AskChappy app via npm run start'
npm run start
