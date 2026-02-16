# Comprehensive Container Check and Cleanup Script
# 综合容器检查和清理脚本
# 此脚本会检查所有 jachin 相关容器，识别重复项，并提供清理建议

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Docker 容器检查和清理工具" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 定义预期的容器（根据 docker-compose.dev.yml）
$expectedContainers = @{
    "jachin-redis-dev" = @{Service="Redis"; Port=6379; Required=$true}
    "jachin-mqtt-dev" = @{Service="MQTT Broker"; Port=1883; Required=$true}
    "jachin-dapr-placement-dev" = @{Service="Dapr Placement"; Port=6050; Required=$true}
    "jachin-dapr-scheduler-dev" = @{Service="Dapr Scheduler"; Port=6060; Required=$true}
}

# 不应该存在的容器（应该使用本地服务）
$unexpectedContainers = @(
    "jachin-postgres",
    "jachin-postgr",
    "jachin-qdrant"
)

Write-Host "[步骤 1/5] 获取所有 jachin 相关容器..." -ForegroundColor Cyan
try {
    $allContainers = docker ps -a --filter "name=jachin" --format "{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}" 2>&1
    
    if ($LASTEXITCODE -ne 0 -or $allContainers -match "error|Cannot connect") {
        Write-Host "  [错误] 无法连接到 Docker" -ForegroundColor Red
        Write-Host "  [信息] 请确保 Docker Desktop 正在运行" -ForegroundColor Yellow
        exit 1
    }
    
    if ($null -eq $allContainers) {
        $allContainers = @()
    }
    
    Write-Host "  找到 $($allContainers.Count) 个容器" -ForegroundColor Gray
} catch {
    Write-Host "  [错误] 获取容器列表失败: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[步骤 2/5] 分析容器状态..." -ForegroundColor Cyan

$containerMap = @{}
$duplicates = @{}
$stoppedContainers = @()
$runningContainers = @()
$unexpectedFound = @()

foreach ($line in $allContainers) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    
    $parts = $line -split '\|'
    if ($parts.Count -lt 2) { continue }
    
    $name = $parts[0].Trim()
    $status = $parts[1].Trim()
    $image = if ($parts.Count -gt 2) { $parts[2].Trim() } else { "" }
    $ports = if ($parts.Count -gt 3) { $parts[3].Trim() } else { "" }
    
    $containerMap[$name] = @{
        Name = $name
        Status = $status
        Image = $image
        Ports = $ports
        IsRunning = $status -match "Up|Running"
        IsStopped = $status -match "Exited|Stopped|Created"
        IsRestarting = $status -match "Restarting"
    }
    
    # 检查重复项（基于服务类型）
    $baseName = $name -replace '-dev$', '' -replace '^jachin-', ''
    if (-not $duplicates.ContainsKey($baseName)) {
        $duplicates[$baseName] = @()
    }
    $duplicates[$baseName] += $name
    
    # 分类容器
    if ($containerMap[$name].IsRunning) {
        $runningContainers += $name
    } elseif ($containerMap[$name].IsStopped) {
        $stoppedContainers += $name
    }
    
    # 检查不应该存在的容器
    foreach ($unexpected in $unexpectedContainers) {
        if ($name -match $unexpected) {
            $unexpectedFound += $name
        }
    }
}

Write-Host ""
Write-Host "[步骤 3/5] 检查重复容器..." -ForegroundColor Cyan
$foundDuplicates = $false
foreach ($key in $duplicates.Keys) {
    if ($duplicates[$key].Count -gt 1) {
        $foundDuplicates = $true
        Write-Host "  [警告] 发现重复项 '$key':" -ForegroundColor Yellow
        foreach ($dupName in $duplicates[$key]) {
            $info = $containerMap[$dupName]
            $statusColor = if ($info.IsRunning) { "Green" } elseif ($info.IsStopped) { "Yellow" } else { "Red" }
            Write-Host "    - $dupName" -ForegroundColor $statusColor
            Write-Host "      状态: $($info.Status)" -ForegroundColor Gray
        }
    }
}

if (-not $foundDuplicates) {
    Write-Host "  [OK] 未发现重复容器" -ForegroundColor Green
}

Write-Host ""
Write-Host "[步骤 4/5] 检查预期容器状态..." -ForegroundColor Cyan

$missingContainers = @()
$wrongStatusContainers = @()

foreach ($expectedName in $expectedContainers.Keys) {
    $expected = $expectedContainers[$expectedName]
    $found = $containerMap.ContainsKey($expectedName)
    
    if (-not $found) {
        Write-Host "  [缺失] $($expected.Service) ($expectedName)" -ForegroundColor Red
        $missingContainers += @{Name=$expectedName; Service=$expected.Service}
    } else {
        $info = $containerMap[$expectedName]
        if ($info.IsRunning) {
            Write-Host "  [运行中] $($expected.Service) ($expectedName)" -ForegroundColor Green
        } elseif ($info.IsRestarting) {
            Write-Host "  [重启中] $($expected.Service) ($expectedName)" -ForegroundColor Red
            $wrongStatusContainers += @{Name=$expectedName; Service=$expected.Service; Status=$info.Status}
        } else {
            Write-Host "  [已停止] $($expected.Service) ($expectedName) - $($info.Status)" -ForegroundColor Yellow
            $wrongStatusContainers += @{Name=$expectedName; Service=$expected.Service; Status=$info.Status}
        }
    }
}

Write-Host ""
Write-Host "[步骤 5/5] 检查不应该存在的容器..." -ForegroundColor Cyan

if ($unexpectedFound.Count -gt 0) {
    Write-Host "  [警告] 发现不应该存在的容器（应使用本地服务）:" -ForegroundColor Yellow
    foreach ($unexpected in $unexpectedFound) {
        $info = $containerMap[$unexpected]
        Write-Host "    - $unexpected ($($info.Status))" -ForegroundColor Yellow
        Write-Host "      建议: 删除此容器，使用本地安装的服务" -ForegroundColor Gray
    }
} else {
    Write-Host "  [OK] 未发现不应该存在的容器" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  检查本地服务（不在 Docker 中）" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 PostgreSQL (本地)
Write-Host "  检查 PostgreSQL (本地, 端口 5432)..." -ForegroundColor Gray
$pgPort = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($pgPort) {
    Write-Host "    [OK] PostgreSQL - 端口 5432 已占用（可能正在运行）" -ForegroundColor Green
} else {
    Write-Host "    [警告] PostgreSQL - 端口 5432 未占用" -ForegroundColor Yellow
}

# 检查 Qdrant (本地)
Write-Host "  检查 Qdrant (本地, 端口 6333)..." -ForegroundColor Gray
$qdrantPort = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($qdrantPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "    [OK] Qdrant - 运行中且健康" -ForegroundColor Green
        }
    } catch {
        try {
            $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($health.StatusCode -eq 200) {
                Write-Host "    [OK] Qdrant - 运行中且健康" -ForegroundColor Green
            }
        } catch {
            Write-Host "    [警告] Qdrant - 端口开放但健康检查失败" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "    [警告] Qdrant - 端口 6333 未占用" -ForegroundColor Yellow
}

# 检查 Backend API (本地)
Write-Host "  检查 Backend API (本地, 端口 18888)..." -ForegroundColor Gray
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$backendPort = Get-NetTCPConnection -LocalPort $appPort -ErrorAction SilentlyContinue
if ($backendPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:$appPort/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "    [OK] Backend API - 运行在端口 $appPort" -ForegroundColor Green
        }
    } catch {
        Write-Host "    [警告] Backend API - 端口 $appPort 开放但健康检查失败" -ForegroundColor Yellow
    }
} else {
    Write-Host "    [信息] Backend API - 未运行在端口 $appPort（正常，需要手动启动）" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  清理建议" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$cleanupCommands = @()

# 1. 删除停止的容器
if ($stoppedContainers.Count -gt 0) {
    Write-Host "[1] 停止的容器（可以安全删除）:" -ForegroundColor Yellow
    foreach ($stopped in $stoppedContainers) {
        Write-Host "    - $stopped" -ForegroundColor Gray
        $cleanupCommands += "docker rm -f $stopped"
    }
    Write-Host ""
}

# 2. 删除不应该存在的容器
if ($unexpectedFound.Count -gt 0) {
    Write-Host "[2] 不应该存在的容器（应删除，使用本地服务）:" -ForegroundColor Red
    foreach ($unexpected in $unexpectedFound) {
        Write-Host "    - $unexpected" -ForegroundColor Yellow
        $cleanupCommands += "docker stop $unexpected; docker rm $unexpected"
    }
    Write-Host ""
}

# 3. 修复重复容器（保留 -dev 后缀的，删除其他的）
if ($foundDuplicates) {
    Write-Host "[3] 重复容器处理建议:" -ForegroundColor Yellow
    foreach ($key in $duplicates.Keys) {
        if ($duplicates[$key].Count -gt 1) {
            $devContainer = $duplicates[$key] | Where-Object { $_ -match '-dev$' }
            $otherContainers = $duplicates[$key] | Where-Object { $_ -notmatch '-dev$' }
            
            if ($devContainer -and $otherContainers.Count -gt 0) {
                Write-Host "    保留: $devContainer" -ForegroundColor Green
                foreach ($other in $otherContainers) {
                    Write-Host "    删除: $other" -ForegroundColor Red
                    $cleanupCommands += "docker stop $other; docker rm $other"
                }
            }
        }
    }
    Write-Host ""
}

# 4. 启动缺失或停止的容器
if ($missingContainers.Count -gt 0 -or $wrongStatusContainers.Count -gt 0) {
    Write-Host "[4] 需要启动的容器:" -ForegroundColor Yellow
    foreach ($missing in $missingContainers) {
        Write-Host "    - $($missing.Service) ($($missing.Name))" -ForegroundColor Gray
    }
    foreach ($wrong in $wrongStatusContainers) {
        Write-Host "    - $($wrong.Service) ($($wrong.Name)) - 当前状态: $($wrong.Status)" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "    启动命令:" -ForegroundColor Gray
    Write-Host "    docker-compose -f docker-compose.dev.yml -p jachin-dev up -d" -ForegroundColor DarkGray
    Write-Host ""
}

# 5. 修复 Dapr Scheduler
$schedulerNeedsFix = $false
foreach ($missing in $missingContainers) {
    if ($missing.Name -eq "jachin-dapr-scheduler-dev") {
        $schedulerNeedsFix = $true
        break
    }
}
if (-not $schedulerNeedsFix) {
    foreach ($wrong in $wrongStatusContainers) {
        if ($wrong.Name -eq "jachin-dapr-scheduler-dev") {
            $schedulerNeedsFix = $true
            break
        }
    }
}
if ($schedulerNeedsFix) {
    Write-Host "[5] Dapr Scheduler 需要修复:" -ForegroundColor Yellow
    Write-Host "    运行: .\scripts\restart_dapr_scheduler.ps1" -ForegroundColor Gray
    Write-Host ""
}

if ($cleanupCommands.Count -eq 0 -and $missingContainers.Count -eq 0 -and $wrongStatusContainers.Count -eq 0) {
    Write-Host "  [OK] 无需清理，所有容器状态正常！" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  快速清理命令" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    
    if ($cleanupCommands.Count -gt 0) {
        Write-Host "# 执行以下命令清理容器:" -ForegroundColor Gray
        foreach ($cmd in $cleanupCommands) {
            Write-Host $cmd -ForegroundColor DarkGray
        }
        Write-Host ""
        
        $response = Read-Host "是否现在执行清理命令? (Y/N)"
        if ($response -eq "Y" -or $response -eq "y") {
            Write-Host ""
            Write-Host "执行清理命令..." -ForegroundColor Cyan
            foreach ($cmd in $cleanupCommands) {
                Write-Host "执行: $cmd" -ForegroundColor Gray
                # PowerShell 中 && 需要分开执行
                if ($cmd -match ';') {
                    $parts = $cmd -split ';'
                    foreach ($part in $parts) {
                        $part = $part.Trim()
                        if ($part) {
                            Invoke-Expression $part
                        }
                    }
                } else {
                    Invoke-Expression $cmd
                }
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  [OK] 成功" -ForegroundColor Green
                } else {
                    Write-Host "  [警告] 命令执行失败（可能容器已不存在）" -ForegroundColor Yellow
                }
            }
            Write-Host ""
            Write-Host "[完成] 清理完成" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  有用的命令" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  列出所有容器: docker ps -a --filter 'name=jachin'" -ForegroundColor Gray
Write-Host "  查看容器日志: docker logs [container-name]" -ForegroundColor Gray
Write-Host "  启动所有服务: docker-compose -f docker-compose.dev.yml -p jachin-dev up -d" -ForegroundColor Gray
Write-Host "  停止所有服务: docker-compose -f docker-compose.dev.yml -p jachin-dev down" -ForegroundColor Gray
Write-Host "  删除所有停止的容器: docker container prune --filter 'name=jachin' -f" -ForegroundColor Gray
Write-Host ""
