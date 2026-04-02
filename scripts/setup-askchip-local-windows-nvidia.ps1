$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $root 'services/askchip-api'
$venvDir = Join-Path $apiDir '.venv'
$pythonExe = Join-Path $venvDir 'Scripts/python.exe'
$pipExe = Join-Path $venvDir 'Scripts/pip.exe'

if (!(Test-Path $venvDir)) {
  Write-Host 'Creating dedicated backend virtual environment at services/askchip-api/.venv'
  py -3.11 -m venv $venvDir
}

Write-Host 'Upgrading pip/setuptools/wheel in dedicated .venv'
& $pythonExe -m pip install --upgrade pip setuptools wheel

Write-Host 'Installing AskChip API dependencies'
Push-Location $apiDir
& $pipExe install -e ".[dev]"

Write-Host 'Replacing CPU ONNX Runtime with GPU ONNX Runtime'
& $pipExe uninstall -y onnxruntime
& $pipExe install --upgrade onnxruntime-gpu

Write-Host 'Validating available ONNX Runtime execution providers'
& $pythonExe -c "import onnxruntime as ort; providers = ort.get_available_providers(); print('ONNX providers:', providers); assert 'CUDAExecutionProvider' in providers, 'CUDAExecutionProvider is not available. Verify NVIDIA driver/CUDA runtime and reinstall onnxruntime-gpu.'"
Pop-Location

Write-Host ''
Write-Host 'Setup completed.'
Write-Host "Activate this environment before running AskChip API:"
Write-Host "  $venvDir\Scripts\Activate.ps1"
