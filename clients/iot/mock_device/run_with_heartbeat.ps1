# Mock IoT Device with Heartbeat - PowerShell 启动脚本
# 持续运行并定期发送心跳，自动过滤 scheduler 错误日志

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Mock IoT Device - With Heartbeat" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 切换到项目根目录
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Join-Path $scriptPath "..\..\.."
Set-Location $projectRoot

# 检查并激活 conda 环境
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) {
    Write-Host "[ERROR] Python not found. Please activate conda environment first." -ForegroundColor Red
    Write-Host "   Run: conda activate jachin-dev" -ForegroundColor Yellow
    exit 1
}

# 验证是否在正确的 conda 环境中
$pythonPath = python -c "import sys; print(sys.executable)" 2>&1
if ($pythonPath -notmatch "jachin-dev") {
    Write-Host "[WARN] Python is not from jachin-dev environment." -ForegroundColor Yellow
    Write-Host "   Current Python: $pythonPath" -ForegroundColor Gray
    Write-Host "   Attempting to activate jachin-dev..." -ForegroundColor Yellow
    
    # 尝试激活环境
    conda activate jachin-dev
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to activate jachin-dev environment." -ForegroundColor Red
        Write-Host "   Please run: conda activate jachin-dev" -ForegroundColor Yellow
        exit 1
    }
}

# 验证 dapr 模块是否可用
$daprCheck = python -c "from dapr.clients import DaprClient; print('OK')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Dapr SDK not found in current Python environment." -ForegroundColor Red
    Write-Host "   Please install: pip install dapr-ext-grpc" -ForegroundColor Yellow
    Write-Host "   Or run: .\install_dependencies.bat" -ForegroundColor Yellow
    exit 1
}

# 检查 Dapr CLI 是否安装
if (-not (Get-Command dapr -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Dapr CLI not found. Please install Dapr first." -ForegroundColor Red
    Write-Host "   Visit: https://docs.dapr.io/getting-started/install-dapr-cli/" -ForegroundColor Yellow
    exit 1
}

# 检查端口是否被占用
$daprHttpPort = 3501
$portCheck = netstat -ano | Select-String ":$daprHttpPort\s"
if ($portCheck) {
    Write-Host "[WARN] Port $daprHttpPort is already in use!" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Checking for existing Dapr applications..." -ForegroundColor Blue
    $existingApp = dapr list 2>&1 | Select-String "mock-iot-device"
    if ($existingApp) {
        Write-Host "[INFO] Found existing mock-iot-device application" -ForegroundColor Cyan
        Write-Host "  Stopping it first..." -ForegroundColor Gray
        dapr stop --app-id mock-iot-device 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "  [OK] Stopped existing application" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Port $daprHttpPort is in use by another process." -ForegroundColor Red
        Write-Host ""
        Write-Host "To diagnose the issue, run:" -ForegroundColor Yellow
        Write-Host "  .\check_port.ps1 -Port $daprHttpPort" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "To clean up the port, run:" -ForegroundColor Yellow
        Write-Host "  .\cleanup_port.ps1 -Port $daprHttpPort" -ForegroundColor Cyan
        Write-Host ""
        exit 1
    }
}

Write-Host "Starting Mock IoT Device with Dapr and Heartbeat..." -ForegroundColor Blue
Write-Host ""

# 日志过滤：减少 scheduler 错误输出频率
$script:schedulerErrorLastShown = 0
$schedulerErrorInterval = 60  # 每60秒显示一次警告

# 使用 Dapr Run 启动（带心跳版本）
& dapr run `
  --app-id mock-iot-device `
  --app-port 8001 `
  --dapr-http-port 3501 `
  --dapr-grpc-port 50002 `
  --resources-path ./dapr/components `
  --config ./dapr/config/config.yaml `
  --log-level error `
  -- python clients/iot/mock_device/main_with_heartbeat.py 2>&1 | 
  ForEach-Object {
      $line = $_
      $now = [DateTimeOffset]::Now.ToUnixTimeSeconds()
      
      # 过滤 scheduler 连接错误（已知的 Dapr 1.16.5 限制）
      if ($line -match "Failed to connect to scheduler host" -or $line -match "scheduler.watchhosts") {
          if (($now - $script:schedulerErrorLastShown) -ge $schedulerErrorInterval) {
              Write-Host "[WARN] Scheduler connection retrying (harmless, known Dapr limitation)" -ForegroundColor DarkYellow
              $script:schedulerErrorLastShown = $now
          }
          return
      }
      
      # 显示其他输出
      Write-Host $line
  }

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] Failed to start Mock IoT Device" -ForegroundColor Red
    exit 1
}
