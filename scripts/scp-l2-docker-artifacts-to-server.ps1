# =============================================================================
# 将 L2 Docker 产物上传到 ECS（默认与 L1 同机 47.86.39.173，换机改 $DeployHost）
# 前置：本机已 docker build -t jachin-l2:latest，已 docker save 为 jachin-l2-latest.tar 或 .tar.gz
# 优先 deploy\l2-ecs-bundle\ 下 compose + l2.env；镜像包可在 bundle 或仓库根
# 用法：仓库根目录  .\scripts\scp-l2-docker-artifacts-to-server.ps1
# =============================================================================
$ErrorActionPreference = "Stop"

$DeployHost = "47.86.39.173"
$DeployUser = "root"
$RemoteDir = "/opt/jachin-l2"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $Root

$BundleDir = Join-Path $Root "deploy\l2-ecs-bundle"
$TarGzBundle = Join-Path $BundleDir "jachin-l2-latest.tar.gz"
$TarBundle = Join-Path $BundleDir "jachin-l2-latest.tar"
$TarGzRoot = Join-Path $Root "jachin-l2-latest.tar.gz"
$TarRoot = Join-Path $Root "jachin-l2-latest.tar"

$ComposeBundle = Join-Path $BundleDir "compose.l2.runtime.yml"
$L2EnvBundle = Join-Path $BundleDir "l2.env"
$ComposeDocker = Join-Path $Root "docker\compose.l2.runtime.yml"
$L2EnvDocker = Join-Path $Root "docker\l2.env"

$Compose = if (Test-Path $ComposeBundle) { $ComposeBundle } else { $ComposeDocker }
$L2Env = if (Test-Path $L2EnvBundle) { $L2EnvBundle } else { $L2EnvDocker }

Write-Host "[scp-l2] Target: ${DeployUser}@${DeployHost}:${RemoteDir}" -ForegroundColor Cyan

if (-not (Test-Path $Compose)) {
    Write-Host "[ERROR] Missing: $Compose" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $L2Env)) {
    Write-Host "[ERROR] Missing l2.env. Copy deploy\l2-ecs-bundle\l2.env.example to l2.env" -ForegroundColor Red
    exit 1
}

$ImageFile = $null
if (Test-Path $TarGzBundle) { $ImageFile = $TarGzBundle }
elseif (Test-Path $TarBundle) { $ImageFile = $TarBundle }
elseif (Test-Path $TarGzRoot) { $ImageFile = $TarGzRoot }
elseif (Test-Path $TarRoot) { $ImageFile = $TarRoot }
else {
    Write-Host "[ERROR] Missing jachin-l2-latest.tar.gz or .tar (docker save after build)" -ForegroundColor Red
    exit 1
}

$RemoteTarget = "${DeployUser}@${DeployHost}:${RemoteDir}/"
ssh "${DeployUser}@${DeployHost}" "mkdir -p ${RemoteDir}"

Write-Host "[scp-l2] Upload image: $(Split-Path $ImageFile -Leaf)" -ForegroundColor Gray
scp $ImageFile $RemoteTarget

Write-Host "[scp-l2] Upload compose.l2.runtime.yml" -ForegroundColor Gray
scp $Compose "${DeployUser}@${DeployHost}:${RemoteDir}/compose.l2.runtime.yml"

Write-Host "[scp-l2] Upload l2.env" -ForegroundColor Gray
scp $L2Env "${DeployUser}@${DeployHost}:${RemoteDir}/l2.env"

$UpScript = Join-Path $BundleDir "server-l2-up.sh"
if (Test-Path $UpScript) {
    Write-Host "[scp-l2] Upload server-l2-up.sh" -ForegroundColor Gray
    scp $UpScript "${DeployUser}@${DeployHost}:${RemoteDir}/server-l2-up.sh"
}

Write-Host ""
Write-Host "[OK] Done. On server:" -ForegroundColor Green
Write-Host "  cd ${RemoteDir}" -ForegroundColor Gray
Write-Host "  docker load -i jachin-l2-latest.tar   # or gunzip + load inner tar if nested" -ForegroundColor Gray
Write-Host "  sed -i 's/\r$//' server-l2-up.sh && chmod +x server-l2-up.sh && ./server-l2-up.sh" -ForegroundColor Gray
Write-Host "  Open security group TCP 18888" -ForegroundColor DarkGray
Write-Host ""
