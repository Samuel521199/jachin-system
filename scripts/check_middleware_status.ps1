# Check middleware services status
# 检查中间件服务状态

$separatorLine = '========================================'

Write-Host $separatorLine -ForegroundColor Cyan
Write-Host 'Middleware Services Status Check' -ForegroundColor Cyan
Write-Host $separatorLine -ForegroundColor Cyan
Write-Host ''

# Check Docker Desktop
$checkDockerMsg = '[1/6] Checking Docker Desktop...'
Write-Host $checkDockerMsg -ForegroundColor Yellow
try {
    docker ps | Out-Null
    $dockerOkMsg = '[OK] Docker Desktop is running'
    Write-Host $dockerOkMsg -ForegroundColor Green
} catch {
    $dockerErrorMsg = '[ERROR] Docker Desktop is not running!'
    Write-Host $dockerErrorMsg -ForegroundColor Red
    Write-Host 'Please start Docker Desktop first.' -ForegroundColor Red
    exit 1
}
Write-Host ''

# Check all containers
$checkContainersMsg = '[2/6] Checking container status...'
Write-Host $checkContainersMsg -ForegroundColor Yellow
$filterName = 'name=jachin-'
$formatTable = 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker ps --filter $filterName --format $formatTable
Write-Host ''

# Check PostgreSQL
$checkPostgresMsg = '[3/6] Checking PostgreSQL...'
Write-Host $checkPostgresMsg -ForegroundColor Yellow
$postgresFilter = 'name=jachin-postgres'
$postgresFormat = '{{.Names}}'
$postgresRunning = docker ps --filter $postgresFilter --format $postgresFormat | Select-String 'jachin-postgres'
if ($postgresRunning) {
    $postgresOkMsg = '[OK] PostgreSQL container is running'
    Write-Host $postgresOkMsg -ForegroundColor Green
    try {
        docker exec jachin-postgres pg_isready -U jachin | Out-Null
        $postgresReadyMsg = '[OK] PostgreSQL is ready to accept connections'
        Write-Host $postgresReadyMsg -ForegroundColor Green
    } catch {
        $postgresWarnMsg = '[WARNING] PostgreSQL container running but not ready yet'
        Write-Host $postgresWarnMsg -ForegroundColor Yellow
    }
} else {
    $postgresErrorMsg = '[ERROR] PostgreSQL container is not running'
    Write-Host $postgresErrorMsg -ForegroundColor Red
}
Write-Host ''

# Check Qdrant
$checkQdrantMsg = '[4/6] Checking Qdrant...'
Write-Host $checkQdrantMsg -ForegroundColor Yellow
$qdrantFilter = 'name=jachin-qdrant'
$qdrantFormat = '{{.Names}}'
$qdrantRunning = docker ps --filter $qdrantFilter --format $qdrantFormat | Select-String 'jachin-qdrant'
if ($qdrantRunning) {
    $qdrantOkMsg = '[OK] Qdrant container is running'
    Write-Host $qdrantOkMsg -ForegroundColor Green
    try {
        $response = Invoke-WebRequest -Uri 'http://localhost:6333/health' -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            $qdrantApiMsg = '[OK] Qdrant API is accessible'
            Write-Host $qdrantApiMsg -ForegroundColor Green
        }
    } catch {
        $qdrantWarnMsg = '[WARNING] Qdrant container running but API not responding yet'
        Write-Host $qdrantWarnMsg -ForegroundColor Yellow
    }
} else {
    $qdrantErrorMsg = '[ERROR] Qdrant container is not running'
    Write-Host $qdrantErrorMsg -ForegroundColor Red
}
Write-Host ''

# Check Redis
$checkRedisMsg = '[5/6] Checking Redis...'
Write-Host $checkRedisMsg -ForegroundColor Yellow
$redisFilter = 'name=jachin-redis'
$redisFormat = '{{.Names}}'
$redisRunning = docker ps --filter $redisFilter --format $redisFormat | Select-String 'jachin-redis'
if ($redisRunning) {
    $redisOkMsg = '[OK] Redis container is running'
    Write-Host $redisOkMsg -ForegroundColor Green
    try {
        $result = docker exec jachin-redis redis-cli ping 2>&1
        if ($result -match 'PONG') {
            $redisRespondMsg = '[OK] Redis is responding'
            Write-Host $redisRespondMsg -ForegroundColor Green
        }
    } catch {
        $redisWarnMsg = '[WARNING] Redis container running but not responding yet'
        Write-Host $redisWarnMsg -ForegroundColor Yellow
    }
} else {
    $redisErrorMsg = '[ERROR] Redis container is not running'
    Write-Host $redisErrorMsg -ForegroundColor Red
}
Write-Host ''

# Check Ray Head
$checkRayMsg = '[6/6] Checking Ray Head...'
Write-Host $checkRayMsg -ForegroundColor Yellow
$rayFilter = 'name=jachin-ray-head'
$rayFormat = '{{.Names}}'
$rayRunning = docker ps --filter $rayFilter --format $rayFormat | Select-String 'jachin-ray-head'
if ($rayRunning) {
    $rayOkMsg = '[OK] Ray Head container is running'
    Write-Host $rayOkMsg -ForegroundColor Green
    try {
        $response = Invoke-WebRequest -Uri 'http://localhost:8265' -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            $rayDashboardMsg = '[OK] Ray Dashboard is accessible at http://localhost:8265'
            Write-Host $rayDashboardMsg -ForegroundColor Green
        }
    } catch {
        $rayWarnMsg = '[WARNING] Ray Head container running but Dashboard not responding yet'
        Write-Host $rayWarnMsg -ForegroundColor Yellow
    }
} else {
    $rayErrorMsg = '[ERROR] Ray Head container is not running'
    Write-Host $rayErrorMsg -ForegroundColor Red
}
Write-Host ''

# Check Dapr Placement
$checkDaprMsg = '[Bonus] Checking Dapr Placement...'
Write-Host $checkDaprMsg -ForegroundColor Yellow
$daprFilter = 'name=jachin-dapr-placement'
$daprFormat = '{{.Names}}'
$daprRunning = docker ps --filter $daprFilter --format $daprFormat | Select-String 'jachin-dapr-placement'
if ($daprRunning) {
    $daprOkMsg = '[OK] Dapr Placement container is running'
    Write-Host $daprOkMsg -ForegroundColor Green
} else {
    $daprWarnMsg = '[WARNING] Dapr Placement container is not running (optional service)'
    Write-Host $daprWarnMsg -ForegroundColor Yellow
}
Write-Host ''

# Summary
Write-Host $separatorLine -ForegroundColor Cyan
Write-Host 'Summary' -ForegroundColor Cyan
Write-Host $separatorLine -ForegroundColor Cyan

$allServices = @(
    @{Name='PostgreSQL'; Container='jachin-postgres'; Port=5432},
    @{Name='Qdrant'; Container='jachin-qdrant'; Port=6333},
    @{Name='Redis'; Container='jachin-redis'; Port=6379},
    @{Name='Ray Head'; Container='jachin-ray-head'; Port=8265},
    @{Name='Dapr Placement'; Container='jachin-dapr-placement'; Port=50005}
)

$runningCount = 0
$totalServices = $allServices.Count

foreach ($service in $allServices) {
    $containerName = $service.Container
    $serviceName = $service.Name
    $filterArg = 'name=' + $containerName
    $formatArg = '{{.Names}}'
    $isRunning = docker ps --filter $filterArg --format $formatArg | Select-String $containerName
    if ($isRunning) {
        $runningCount++
        $okMsg = '[OK] ' + $serviceName + ' - Running'
        Write-Host $okMsg -ForegroundColor Green
    } else {
        $notRunningMsg = '[ ] ' + $serviceName + ' - Not running'
        Write-Host $notRunningMsg -ForegroundColor Red
    }
}

Write-Host ''
$statusMsg = 'Running: ' + $runningCount + '/' + $totalServices + ' services'
if ($runningCount -eq $totalServices) {
    Write-Host $statusMsg -ForegroundColor Green
} else {
    Write-Host $statusMsg -ForegroundColor Yellow
}
Write-Host ''

$separator = '========================================'
$nextStepsMsg = 'Next steps:'
$startServicesMsg = 'To start all services:'
$dockerCommand = '  docker-compose -f docker-compose.minimal.yml up -d'

if ($runningCount -eq $totalServices) {
    Write-Host $separator -ForegroundColor Green
    Write-Host 'All middleware services are running!' -ForegroundColor Green
    Write-Host $separator -ForegroundColor Green
    Write-Host ''
    Write-Host $nextStepsMsg -ForegroundColor Yellow
    Write-Host '  1. Initialize database: .\installer\init_database.ps1' -ForegroundColor Gray
    Write-Host '  2. Configure environment: Copy .env.example to .env and edit' -ForegroundColor Gray
    Write-Host '  3. Start backend: .\scripts\start.ps1' -ForegroundColor Gray
} else {
    Write-Host $separator -ForegroundColor Yellow
    Write-Host 'Some services are not running' -ForegroundColor Yellow
    Write-Host $separator -ForegroundColor Yellow
    Write-Host ''
    Write-Host $startServicesMsg -ForegroundColor Yellow
    Write-Host $dockerCommand -ForegroundColor Gray
}
Write-Host ''
