# Export YOLOv8n to ONNX in a throwaway environment.
#
#   .\scripts\export_yolo.ps1
#
# WHY A SEPARATE ENVIRONMENT
# `ultralytics` needs PyTorch, and it depends on `opencv-python` - which installs
# the same `cv2` module as the `opencv-contrib-python` the agent uses. Installing
# it into the main venv risks replacing a working OpenCV with a different build.
#
# So this creates .venv-export, exports the model, and deletes the environment.
# The resulting ONNX file lives in %LOCALAPPDATA%\FocusGuard\models and the agent
# loads it through onnxruntime-directml. PyTorch never becomes a runtime
# dependency - see requirements/agent.txt.
#
# Run once. Takes several minutes, mostly downloading PyTorch.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exportVenv = Join-Path $root ".venv-export"

Write-Host "FocusGuard - YOLOv8n ONNX export" -ForegroundColor Cyan
Write-Host ("=" * 50)

if (Test-Path $exportVenv) {
    Write-Host "Reusing existing export environment at $exportVenv"
} else {
    Write-Host "Creating throwaway environment ..."
    python -m venv $exportVenv

    $py = Join-Path $exportVenv "Scripts\python.exe"

    # The CPU-only index avoids the ~2.5 GB CUDA build, which is useless here:
    # this machine has an AMD GPU and the export runs once on the CPU anyway.
    Write-Host "Installing PyTorch (CPU build) ..."
    & $py -m pip install --quiet --index-url https://download.pytorch.org/whl/cpu torch torchvision

    Write-Host "Installing ultralytics ..."
    & $py -m pip install --quiet ultralytics onnx
}

$py = Join-Path $exportVenv "Scripts\python.exe"
$script = Join-Path $PSScriptRoot "export_yolo.py"

Write-Host "`nExporting ..." -ForegroundColor Cyan
& $py $script @args

if ($LASTEXITCODE -ne 0) {
    Write-Host "Export failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`nRemoving the throwaway environment ..." -ForegroundColor Cyan
Remove-Item -Recurse -Force $exportVenv

Write-Host "Done. Phone detection is now available to the agent." -ForegroundColor Green
