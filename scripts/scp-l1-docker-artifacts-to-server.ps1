# =============================================================================
# 将方式 B 产物上传到 L1 云端 ECS（IP 写死，换服务器时只改本脚本顶部变量）
# 前置：本机已生成 jachin-l1-latest.tar.gz（或 .tar）
# 优先使用 deploy\l1-ecs-bundle\ 下的 compose + l1.env（已填 ECS 参数）；否则回退 docker\
# 镜像包可放在 deploy\l1-ecs-bundle\ 或仓库根目录
# 用法：在仓库根目录  .\scripts\scp-l1-docker-artifacts-to-server.ps1
# 依赖：OpenSSH 客户端（scp / ssh），与服务器 root 密钥或密码登录
# =============================================================================
$ErrorActionPreference = "Stop"

$DeployHost = "47.86.39.173"
$DeployUser = "root"
$RemoteDir = "/opt/jachin-l1"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $Root

$BundleDir = Join-Path $Root "deploy\l1-ecs-bundle"
$TarGzBundle = Join-Path $BundleDir "jachin-l1-latest.tar.gz"
$TarBundle = Join-Path $BundleDir "jachin-l1-latest.tar"
$TarGzRoot = Join-Path $Root "jachin-l1-latest.tar.gz"
$TarRoot = Join-Path $Root "jachin-l1-latest.tar"

$ComposeBundle = Join-Path $BundleDir "compose.l1.runtime.yml"
$L1EnvBundle = Join-Path $BundleDir "l1.env"
$ComposeDocker = Join-Path $Root "docker\compose.l1.runtime.yml"
$L1EnvDocker = Join-Path $Root "docker\l1.env"

$Compose = if (Test-Path $ComposeBundle) { $ComposeBundle } else { $ComposeDocker }
$L1Env = if (Test-Path $L1EnvBundle) { $L1EnvBundle } else { $L1EnvDocker }

Write-Host "[scp-l1] 目标: ${DeployUser}@${DeployHost}:${RemoteDir}" -ForegroundColor Cyan

if (-not (Test-Path $Compose)) {
    Write-Host "[ERROR] 未找到: $Compose" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $L1Env)) {
    Write-Host "[ERROR] 未找到 l1.env。请使用 deploy\l1-ecs-bundle\l1.env（从 l1.env.example 复制）或 docker\l1.env" -ForegroundColor Red
    exit 1
}
$ImageFile = $null
if (Test-Path $TarGzBundle) { $ImageFile = $TarGzBundle }
elseif (Test-Path $TarBundle) { $ImageFile = $TarBundle }
elseif (Test-Path $TarGzRoot) { $ImageFile = $TarGzRoot }
elseif (Test-Path $TarRoot) { $ImageFile = $TarRoot }
else {
    Write-Host "[ERROR] 未找到镜像包。请 docker save 后放到 deploy\l1-ecs-bundle\ 或仓库根目录：jachin-l1-latest.tar.gz / .tar" -ForegroundColor Red
    exit 1
}

$RemoteTarget = "${DeployUser}@${DeployHost}:${RemoteDir}/"
Write-Host "[scp-l1] 确保远程目录存在..." -ForegroundColor Gray
ssh "${DeployUser}@${DeployHost}" "mkdir -p ${RemoteDir}"

Write-Host "[scp-l1] 上传镜像: $(Split-Path $ImageFile -Leaf)" -ForegroundColor Gray
scp $ImageFile $RemoteTarget

Write-Host "[scp-l1] 上传 compose.l1.runtime.yml" -ForegroundColor Gray
scp $Compose "${DeployUser}@${DeployHost}:${RemoteDir}/compose.l1.runtime.yml"

Write-Host "[scp-l1] 上传 l1.env" -ForegroundColor Gray
scp $L1Env "${DeployUser}@${DeployHost}:${RemoteDir}/l1.env"

$ServerScript = Join-Path $BundleDir "server-load-and-up.sh"
if (Test-Path $ServerScript) {
    Write-Host "[scp-l1] 上传 server-load-and-up.sh" -ForegroundColor Gray
    scp $ServerScript "${DeployUser}@${DeployHost}:${RemoteDir}/server-load-and-up.sh"
}

Write-Host ""
Write-Host "[OK] 上传完成。SSH 登录后执行：" -ForegroundColor Green
Write-Host "  ssh ${DeployUser}@${DeployHost}" -ForegroundColor Gray
Write-Host "  cd ${RemoteDir} && chmod +x server-load-and-up.sh && ./server-load-and-up.sh" -ForegroundColor Gray
Write-Host "  Tip: manual gunzip / docker load / compose -> deploy\l1-ecs-bundle\README.txt" -ForegroundColor DarkGray
Write-Host ""
