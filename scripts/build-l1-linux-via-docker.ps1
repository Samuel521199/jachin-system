# =============================================================================
# Windows 本机：用 Docker 仅作「linux/amd64 构建机」，打出可在 Linux 上直接解压运行的便携包（非 Docker 部署）
# 产物 tar.gz 内含 runtime/node（官方 linux-x64）+ start.sh + Next standalone，类似 Windows 绿色版目录
# 服务器：解压后 ./start.sh，无需 docker、默认无需 apt install node
#
# 前置：Docker Desktop 已安装并启动
# 用法: .\scripts\build-l1-linux-via-docker.ps1
# =============================================================================
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path

Write-Host ""
Write-Host "[L1-docker-build] 仓库根: $Root" -ForegroundColor Cyan

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 未检测到 Docker。请安装 Docker Desktop 并启动。" -ForegroundColor Red
    exit 1
}
docker version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker 不可用（daemon 未启动？）。请先启动 Docker Desktop。" -ForegroundColor Red
    exit 1
}

$Image = "node:20-bookworm"
Write-Host "[L1-docker-build] 拉取镜像: $Image ..." -ForegroundColor Gray
docker pull $Image

# 挂载整仓到 /repo；先 sed 掉 Windows CRLF，再执行打包脚本（避免 bash $'\r'）
# 使用单引号，防止 PowerShell 把 \r$ 中的 $ 当变量展开
$DockerLc = 'sed -i "s/\r$//" /repo/scripts/packaging/l1-linux/start.sh /repo/scripts/build-l1-linux-release.sh /repo/scripts/docker-l1-inner-build.sh 2>/dev/null; bash /repo/scripts/docker-l1-inner-build.sh'
Write-Host "[L1-docker-build] 在容器内构建（平台 linux/amd64），请等待数分钟..." -ForegroundColor Yellow
docker run --rm `
    --platform linux/amd64 `
    -v "${Root}:/repo" `
    -w /repo `
    $Image `
    bash -lc "$DockerLc"

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 容器内构建失败 exit=$LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "[OK] 产物已写入本机: $Root\dist\jachin-l1-linux-amd64-v*.tar.gz" -ForegroundColor Green
Write-Host "  上传（Docker 方式 B）: .\scripts\scp-l1-docker-artifacts-to-server.ps1  （当前 ECS 47.86.39.173）" -ForegroundColor Gray
Write-Host "  详见: docs/L1_LINUX_CLOUD_DEPLOY.md" -ForegroundColor Gray
Write-Host ""
