# 将仓库内 L1 商店可上架的 Skill / MCP / TOOL 批量发布到指定 Nexus（公网或内网）。
#
# 与本地 L1 对齐：
#   - 声明式 SKILL + MCP stub + TOOL 元数据包：与本地相同，走 cloud/nexus/scripts/bulk-publish-store.cjs
#     （npm run store:bulk-publish；TOOL 仅 zip 内 plugin.json，与 MCP stub 同理）
#   - 大目录 jachin pack + publish：publish_l1_store.ps1 用 jachin；本脚本含 filesystem、hr-analyzer4、jachin-system-pilot
# bulk 使用 --continue-on-error：避免最后一项 TOOL 在远端 DB 报错时阻断后续 Wasm 上架
#
# 前置：
#   - pip install -e tools/jachin-cli
#   - cloud/nexus 已 npm install（bulk-publish 用 adm-zip）
#   - 远端 Nexus 进程中的 JACHIN_DEV_TOKEN / JACHIN_DEV_ID 须与本命令使用的 token 一致（否则 401）
#   - 远端建议 NEXUS_AUTO_APPROVE=1，否则 077 下依赖 MCP 可能 pending 导致后续包失败
#
# 用法（仓库根）:
#   $env:JACHIN_DEV_TOKEN = "<与 http://47.86.39.173:3000 上 Nexus 的 JACHIN_DEV_TOKEN 相同>"
#   $env:JACHIN_NEXUS_URL = "http://47.86.39.173:3000"
#   .\scripts\publish-l1-to-remote-nexus.ps1
#
# 可选：仅打印 bulk 任务数、不实际上传
#   .\scripts\publish-l1-to-remote-nexus.ps1 -DryRunBulk

param(
    [string] $NexusUrl = "",
    [switch] $DryRunBulk
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if ($NexusUrl -and $NexusUrl.Trim()) {
    $Nexus = $NexusUrl.Trim().TrimEnd("/")
} elseif ($env:JACHIN_NEXUS_URL -and $env:JACHIN_NEXUS_URL.Trim()) {
    $Nexus = $env:JACHIN_NEXUS_URL.Trim().TrimEnd("/")
} else {
    $Nexus = "http://47.86.39.173:3000"
}

if (-not $DryRunBulk -and -not $env:JACHIN_DEV_TOKEN) {
    Write-Host "请设置 JACHIN_DEV_TOKEN，且必须与目标 Nexus 服务器环境变量 JACHIN_DEV_TOKEN 完全一致（本机 .env.local 的 token 与公网服务器通常不同，会 401）。" -ForegroundColor Red
    exit 1
}

function Publish-JachinDir {
    param([string] $RelDir)
    $dir = Join-Path $Root $RelDir
    if (-not (Test-Path (Join-Path $dir "plugin.json"))) {
        Write-Host "跳过（无 plugin.json）: $RelDir" -ForegroundColor Yellow
        return
    }
    Write-Host "`n=== jachin: $(Split-Path $RelDir -Leaf) ===" -ForegroundColor Cyan
    Push-Location $dir
    try {
        jachin pack
        if ($LASTEXITCODE -ne 0) { throw "jachin pack failed: $RelDir" }
        jachin publish --nexus $Nexus --visibility PUBLIC --price 0
        if ($LASTEXITCODE -ne 0) { throw "jachin publish failed: $RelDir" }
    } finally {
        Pop-Location
    }
}

if ($DryRunBulk) {
    $env:NEXUS_URL = $Nexus
    Push-Location (Join-Path $Root "cloud\nexus")
    try {
        node scripts/bulk-publish-store.cjs --dry-run
        if ($LASTEXITCODE -ne 0) { throw "bulk-publish-store.cjs --dry-run failed" }
    } finally {
        Pop-Location
    }
    Write-Host "`nDryRunBulk：仅统计 bulk 任务体积，未执行任何上传。完整流程顺序：filesystem → bulk → hr-analyzer4 → jachin-system-pilot。" -ForegroundColor Yellow
    exit 0
}

# 1) HR 透析镜依赖的 MCP（bulk 不含此项；须在 Wasm Skill 之前入库以满足 077）
Publish-JachinDir "skills_repo\plugin\com.jachin.hr.filesystem"

# 2) bulk：stub MCP、完整 recruitment、声明式 SKILL、TOOL
$env:NEXUS_URL = $Nexus
Push-Location (Join-Path $Root "cloud\nexus")
try {
    node scripts/bulk-publish-store.cjs --continue-on-error
    if ($LASTEXITCODE -ne 0) { throw "bulk-publish-store.cjs failed" }
} finally {
    Pop-Location
}

# 3) Wasm / 大包 Skill
Publish-JachinDir "skills_repo\hr-analyzer4"
Publish-JachinDir "skills_repo\jachin-system-pilot"

Write-Host "`n完成。商店: $Nexus/store ；若远端未设 NEXUS_AUTO_APPROVE=1，请到 /dashboard/admin/review 审核 pending。" -ForegroundColor Green
