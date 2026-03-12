# 修复数据库问题脚本
# 解决 "database jachin does not exist" 错误

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  修复 PostgreSQL 数据库配置问题" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查容器是否运行（支持多种容器名）
$containerNames = @("jachin-postgres", "jachin-postgres-dev")
$containerName = $null

foreach ($name in $containerNames) {
    $exists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^${name}$"
    if ($exists) {
        $containerName = $name
        break
    }
}

if (-not $containerName) {
    Write-Host "[错误] 未找到 PostgreSQL 容器" -ForegroundColor Red
    Write-Host "[提示] 请先运行: docker-compose -f docker-compose.dev.yml up -d" -ForegroundColor Yellow
    exit 1
}

Write-Host "[信息] 找到容器: $containerName" -ForegroundColor Green

Write-Host "[1/4] 检查容器状态..." -ForegroundColor Cyan
$containerRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^${containerName}$"
if (-not $containerRunning) {
    Write-Host "[警告] 容器未运行，尝试启动..." -ForegroundColor Yellow
    docker start $containerName
    Start-Sleep -Seconds 5
}

Write-Host "[2/4] 检查数据库 jachin_brain 是否存在..." -ForegroundColor Cyan
$dbExists = docker exec $containerName psql -U jachin -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='jachin_brain';" 2>&1
if ($dbExists -match "1") {
    Write-Host "  [OK] 数据库 jachin_brain 已存在" -ForegroundColor Green
} else {
    Write-Host "  [警告] 数据库 jachin_brain 不存在，正在创建..." -ForegroundColor Yellow
    docker exec $containerName psql -U jachin -d postgres -c "CREATE DATABASE jachin_brain;" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] 数据库 jachin_brain 创建成功" -ForegroundColor Green
    } else {
        Write-Host "  [错误] 创建数据库失败" -ForegroundColor Red
    }
}

Write-Host "[3/4] 创建临时数据库 'jachin'（解决当前错误）..." -ForegroundColor Cyan
$jachinDbExists = docker exec $containerName psql -U jachin -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='jachin';" 2>&1
if ($jachinDbExists -match "1") {
    Write-Host "  [OK] 数据库 jachin 已存在" -ForegroundColor Green
} else {
    Write-Host "  [信息] 创建临时数据库 jachin（某些应用可能在使用此名称）..." -ForegroundColor Yellow
    docker exec $containerName psql -U jachin -d postgres -c "CREATE DATABASE jachin;" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] 数据库 jachin 创建成功" -ForegroundColor Green
        Write-Host "  [警告] 这是临时解决方案，建议修复应用配置使用 jachin_brain" -ForegroundColor Yellow
    } else {
        Write-Host "  [错误] 创建数据库失败" -ForegroundColor Red
    }
}

Write-Host "[4/4] 列出所有数据库..." -ForegroundColor Cyan
docker exec $containerName psql -U jachin -d postgres -c "\l" 2>&1

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  修复完成" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "已创建以下数据库:" -ForegroundColor Green
Write-Host "  - jachin_brain (主数据库)" -ForegroundColor Gray
Write-Host "  - jachin (临时数据库，用于解决当前错误)" -ForegroundColor Gray
Write-Host ""
Write-Host "建议后续操作:" -ForegroundColor Yellow
Write-Host "  1. 检查是否有应用在使用错误的数据库名 'jachin'" -ForegroundColor Gray
Write-Host "  2. 更新应用配置，使用正确的数据库名 'jachin_brain'" -ForegroundColor Gray
Write-Host "  3. 检查环境变量 DATABASE_URL 是否正确" -ForegroundColor Gray
Write-Host "  4. 检查 .env 文件中的配置" -ForegroundColor Gray
Write-Host ""
Write-Host "查看实时日志:" -ForegroundColor Cyan
Write-Host "  docker-compose -f docker-compose.dev.yml logs -f postgres" -ForegroundColor Gray
Write-Host ""
Write-Host "若启动时报「对表 skills 权限不足」:" -ForegroundColor Yellow
Write-Host "  运行: .\installer\grant_app_permissions.ps1" -ForegroundColor Gray
Write-Host "  (需以 postgres 等超级用户执行 GRANT，或手动执行脚本生成的 SQL)" -ForegroundColor Gray
