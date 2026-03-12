# Generate gRPC Code from common/protocols/jachin_link.proto
# 从 common/protocols/jachin_link.proto 生成 gRPC 代码

Write-Host "Generating gRPC code from common/protocols/jachin_link.proto..." -ForegroundColor Cyan

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptPath)
Set-Location $projectRoot

$protoFile = "common/protocols/jachin_link.proto"
$pythonOut = "core/transport"
$includePath = "common/protocols"

if (-not (Test-Path $protoFile)) {
    Write-Host "Error: jachin_link.proto not found at $protoFile" -ForegroundColor Red
    exit 1
}

# Check if grpc_tools is installed
try {
    python -m grpc_tools.protoc --version 2>&1 | Out-Null
} catch {
    Write-Host "Error: grpc_tools not installed. Install with: pip install grpcio-tools" -ForegroundColor Red
    exit 1
}

# Ensure output directory exists
New-Item -ItemType Directory -Force -Path $pythonOut | Out-Null

# Generate Python code
Write-Host "Running protoc..." -ForegroundColor Gray
python -m grpc_tools.protoc `
    -I $includePath `
    --python_out=$pythonOut `
    --grpc_python_out=$pythonOut `
    $protoFile

if ($LASTEXITCODE -eq 0) {
    Write-Host "Success! Generated files:" -ForegroundColor Green
    Write-Host "  - $pythonOut/jachin_link_pb2.py" -ForegroundColor Gray
    Write-Host "  - $pythonOut/jachin_link_pb2_grpc.py" -ForegroundColor Gray
} else {
    Write-Host "Error: Failed to generate gRPC code" -ForegroundColor Red
    exit 1
}
