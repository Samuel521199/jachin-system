# Generate gRPC code from common/protocols/jachin_link.proto
# 从 common/protocols/jachin_link.proto 生成 gRPC 代码

$ErrorActionPreference = "Stop"

Write-Host "Generating gRPC code from common/protocols/jachin_link.proto..." -ForegroundColor Cyan

# 切换到项目根目录
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 检查 protoc 是否可用
try {
    $protocVersion = python -m grpc_tools.protoc --version 2>&1
    Write-Host "Using grpc_tools.protoc" -ForegroundColor Green
} catch {
    Write-Host "Error: grpc_tools not installed. Run: pip install grpcio-tools" -ForegroundColor Red
    exit 1
}

# 生成 gRPC 代码
$protoFile = "common/protocols/jachin_link.proto"
$pythonOut = "core/transport"
$grpcOut = "core/transport"
$includePath = "common/protocols"

if (-not (Test-Path $protoFile)) {
    Write-Host "Error: Proto file not found: $protoFile" -ForegroundColor Red
    exit 1
}

Write-Host "Proto file: $protoFile" -ForegroundColor Yellow
Write-Host "Output directory: $pythonOut" -ForegroundColor Yellow

# 确保输出目录存在
New-Item -ItemType Directory -Force -Path $pythonOut | Out-Null

# 执行 protoc 命令
python -m grpc_tools.protoc `
    -I $includePath `
    --python_out=$pythonOut `
    --grpc_python_out=$grpcOut `
    $protoFile

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n✓ gRPC code generated successfully!" -ForegroundColor Green
    Write-Host "  - $pythonOut/jachin_link_pb2.py" -ForegroundColor Gray
    Write-Host "  - $pythonOut/jachin_link_pb2_grpc.py" -ForegroundColor Gray
    
    # 修复 jachin_link_pb2_grpc.py 中的导入问题
    $grpcFile = Join-Path $pythonOut "jachin_link_pb2_grpc.py"
    if (Test-Path $grpcFile) {
        Write-Host "`nFixing imports in jachin_link_pb2_grpc.py..." -ForegroundColor Cyan
        $content = Get-Content $grpcFile -Raw -Encoding UTF8
        
        # 替换相对导入为绝对导入（带回退）
        $oldImport = "import jachin_link_pb2 as jachin__link__pb2"
        $newImport = @"
try:
    from core.transport import jachin_link_pb2 as jachin__link__pb2
except ImportError:
    # 回退到相对导入（用于直接运行）
    import jachin_link_pb2 as jachin__link__pb2
"@
        
        if ($content -match [regex]::Escape($oldImport)) {
            $content = $content -replace [regex]::Escape($oldImport), $newImport
            Set-Content $grpcFile -Value $content -NoNewline -Encoding UTF8
            Write-Host "  ✓ Fixed imports" -ForegroundColor Green
        } else {
            Write-Host "  ℹ Import already fixed or not found" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "`n✗ Failed to generate gRPC code" -ForegroundColor Red
    exit 1
}
