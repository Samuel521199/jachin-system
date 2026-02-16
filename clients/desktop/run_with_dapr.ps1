# Desktop Sprite - 使用 Dapr 启动脚本
# 按照 v2.0 架构，桌面客户端需要运行 Dapr sidecar 来接收命令

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Jachin Desktop Sprite - With Dapr" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 切换到项目根目录
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
# 从 clients\desktop 到项目根目录需要向上两级
# 使用规范化路径解析
$relativePath = Join-Path $scriptPath "..\.."
$projectRoot = [System.IO.Path]::GetFullPath($relativePath)
Set-Location $projectRoot

# 检查 Dapr 是否安装
if (-not (Get-Command dapr -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Dapr CLI not found. Please install Dapr first." -ForegroundColor Red
    Write-Host "   Visit: https://docs.dapr.io/getting-started/install-dapr-cli/" -ForegroundColor Yellow
    exit 1
}

# 检查 Node.js 和 npm
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] npm not found. Please install Node.js first." -ForegroundColor Red
    exit 1
}

# 获取设备ID（主机名）
$deviceId = "$env:COMPUTERNAME"
if (-not $deviceId) {
    $deviceId = hostname
}

Write-Host "Device ID: desktop-$deviceId" -ForegroundColor Gray
Write-Host ""

# Dapr 配置
$appId = "desktop-client"
$appPort = 8002
$daprHttpPort = 3502
$daprGrpcPort = 50003
$componentsPath = Join-Path $projectRoot "dapr\components"
$configPath = Join-Path $projectRoot "dapr\config\config.yaml"

Write-Host "Starting Desktop Sprite with Dapr..." -ForegroundColor Blue
Write-Host "  App ID: $appId" -ForegroundColor Gray
Write-Host "  App Port: $appPort" -ForegroundColor Gray
Write-Host "  Dapr HTTP Port: $daprHttpPort" -ForegroundColor Gray
Write-Host ""

# 检查组件和配置文件
Write-Host "Checking Dapr configuration..." -ForegroundColor Gray
Write-Host "  Components path: $componentsPath" -ForegroundColor DarkGray
Write-Host "  Config path: $configPath" -ForegroundColor DarkGray

if (-not (Test-Path $componentsPath)) {
    Write-Host "[ERROR] Dapr components not found: $componentsPath" -ForegroundColor Red
    Write-Host "  Current directory: $PWD" -ForegroundColor Yellow
    Write-Host "  Script path: $scriptPath" -ForegroundColor Yellow
    Write-Host "  Project root: $projectRoot" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $configPath)) {
    Write-Host "[ERROR] Dapr config not found: $configPath" -ForegroundColor Red
    Write-Host "  Current directory: $PWD" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Dapr configuration found" -ForegroundColor Green

# 检查并停止旧的 desktop-client Dapr 应用
Write-Host "Checking for existing Dapr applications..." -ForegroundColor Gray
$existingApps = dapr list --output json 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
if ($existingApps) {
    $desktopApp = $existingApps | Where-Object { $_.'APP ID' -eq $appId }
    if ($desktopApp) {
        Write-Host "  Found existing $appId application, stopping it..." -ForegroundColor Yellow
        dapr stop --app-id $appId 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "  [OK] Stopped existing application" -ForegroundColor Green
    }
}

# 检查端口占用
Write-Host "Checking port availability..." -ForegroundColor Gray
# 检查 Dapr 端口和应用端口
$portCheck = @($daprHttpPort, $daprGrpcPort, $appPort)
# 检查 Vite 开发服务器端口（默认 1420）
$vitePort = 1420
$portCheck += $vitePort

foreach ($port in $portCheck) {
    $connection = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($connection) {
        # 使用 $processId 而不是 $pid（$pid 是 PowerShell 的只读变量）
        $processId = ($connection | Select-Object -First 1).OwningProcess
        try {
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            $processName = if ($process) { $process.ProcessName } else { "Unknown" }
            Write-Host "  [WARN] Port $port is in use by PID $processId ($processName)" -ForegroundColor Yellow
            
            # 如果是 Node.js/Vite 进程占用 Vite 端口，尝试停止
            if ($port -eq $vitePort) {
                Write-Host "    Stopping process on Vite port $vitePort..." -ForegroundColor DarkGray
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Write-Host "    [OK] Stopped process" -ForegroundColor Green
            } else {
                # 对于其他端口，也尝试停止
                Write-Host "    Attempting to stop process..." -ForegroundColor DarkGray
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
            }
        } catch {
            Write-Host "    [INFO] Process may have already terminated" -ForegroundColor DarkGray
        }
    }
}

# 额外检查：查找所有占用端口 1420 的 Node.js 进程
Write-Host "  Checking for Node.js processes on port $vitePort..." -ForegroundColor Gray
$nodeProcesses = Get-Process -Name "node" -ErrorAction SilentlyContinue
if ($nodeProcesses) {
    foreach ($nodeProc in $nodeProcesses) {
        $nodeConnections = Get-NetTCPConnection -OwningProcess $nodeProc.Id -LocalPort $vitePort -ErrorAction SilentlyContinue
        if ($nodeConnections) {
            Write-Host "    Found Node.js process (PID: $($nodeProc.Id)) on port $vitePort, stopping..." -ForegroundColor Yellow
            Stop-Process -Id $nodeProc.Id -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
    }
}

# 切换到桌面客户端目录
$desktopDir = Join-Path $scriptPath "."

# 检查 secrets.json 是否存在
$secretsPath = Join-Path $projectRoot "dapr\secrets\secrets.json"
if (-not (Test-Path $secretsPath)) {
    Write-Host "[WARN] secrets.json not found, creating empty file..." -ForegroundColor Yellow
    $secretsDir = Split-Path $secretsPath -Parent
    if (-not (Test-Path $secretsDir)) {
        New-Item -ItemType Directory -Force -Path $secretsDir | Out-Null
    }
    @'
{
  "QWEN_API_KEY": ""
}
'@ | Out-File -FilePath $secretsPath -Encoding utf8
    Write-Host "  [OK] Created empty secrets.json at $secretsPath" -ForegroundColor Green
    Write-Host "  [INFO] Please add your API keys to this file if needed" -ForegroundColor Cyan
}

# 使用 Dapr Run 启动
# 重要：Dapr 需要在项目根目录运行（因为 secrets.json 路径是相对于项目根目录的）
# 但 npm 命令需要在 desktop 目录运行，所以使用 --prefix 参数
Write-Host "Starting with Dapr..." -ForegroundColor Blue
Write-Host "  Working directory: $projectRoot" -ForegroundColor DarkGray
Write-Host "  Desktop directory: $desktopDir" -ForegroundColor DarkGray
Write-Host ""

# 切换到项目根目录运行 Dapr
Set-Location $projectRoot

# 日志过滤：减少 scheduler 错误输出频率
$script:schedulerErrorLastShown = 0
$schedulerErrorInterval = 60  # 每60秒显示一次警告

dapr run `
  --app-id $appId `
  --app-port $appPort `
  --dapr-http-port $daprHttpPort `
  --dapr-grpc-port $daprGrpcPort `
  --resources-path $componentsPath `
  --config $configPath `
  --log-level error `
  -- npm --prefix $desktopDir run tauri:dev 2>&1 | 
  ForEach-Object {
      $line = $_
      $now = [DateTimeOffset]::Now.ToUnixTimeSeconds()
      
      # 过滤 scheduler 连接错误（已知的 Dapr 1.16.5 限制）
      # 匹配多种 scheduler 错误格式
      if ($line -match "Failed to connect to scheduler" -or 
          $line -match "scheduler.watchhosts" -or 
          $line -match "scheduler host" -or
          $line -match "dial tcp.*6060") {
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
    Write-Host "[ERROR] Failed to start Desktop Sprite" -ForegroundColor Red
    exit 1
}
