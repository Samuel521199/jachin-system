# 将仓库内可打包的 MCP / Wasm Skill 批量 pack + publish 到 L1 Nexus。
#
# 无法上架到 L1 商店的类型（勿与「商城 SKU」混淆）：
# - core:* / util:* / sys:* 等 Native 工具：由 l3_node loader 内置注册，不通过 Nexus zip 分发。
# - jpp:*：由已订阅的 Wasm 技能包在运行时暴露，无独立「工具商品」包。
# - 仅含 SKILL.md、无 plugin.json+Wasm 的声明式技能：当前 jachin pack 要求 Skill 带入口文件，需另做 Wasm/影子发布方案。
#
# 用法（仓库根目录）:
#   python scripts\gen_l1_mcp_stub_packages.py
#   $env:JACHIN_DEV_TOKEN = "<.env.local 中 JACHIN_DEV_TOKEN 的值>"
#   $env:JACHIN_NEXUS_URL = "http://localhost:3000"
#   .\scripts\publish_l1_store.ps1
#
# 前置: Postgres、Nexus、JACHIN_DEV_ID/TOKEN、pip install -e tools/jachin-cli、pip install httpx
# 建议: NEXUS_AUTO_APPROVE=1（本机），否则 077 下须先审核通过 com.jachin.hr.filesystem 再发 hr-analyzer4

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $env:JACHIN_DEV_TOKEN) {
    Write-Host "请设置环境变量 JACHIN_DEV_TOKEN" -ForegroundColor Red
    exit 1
}
$Nexus = ($env:JACHIN_NEXUS_URL -as [string]).Trim()
if (-not $Nexus) {
    Write-Host "请设置 JACHIN_NEXUS_URL，例如 http://localhost:3000" -ForegroundColor Red
    exit 1
}

$StubsRoot = Join-Path $Root "skills_repo\l1_upload_stubs"
if (-not (Test-Path $StubsRoot)) {
    Write-Host "未找到 skills_repo\l1_upload_stubs，正在生成 MCP 占位包..." -ForegroundColor Yellow
    python (Join-Path $Root "scripts\gen_l1_mcp_stub_packages.py")
    if ($LASTEXITCODE -ne 0) { throw "gen_l1_mcp_stub_packages.py failed" }
}

$StubDirs = @()
if (Test-Path $StubsRoot) {
    $StubDirs = @(Get-ChildItem $StubsRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { $_.FullName })
}

# 顺序：HR 透析镜依赖的 MCP → 其余 stub MCP → HR 大 MCP → Wasm Skills
$SkillDirs = @(
    (Join-Path $Root "skills_repo\plugin\com.jachin.hr.filesystem")
) + $StubDirs + @(
    (Join-Path $Root "skills_repo\plugin\com.jachin.hr.recruitment"),
    (Join-Path $Root "skills_repo\hr-analyzer4"),
    (Join-Path $Root "skills_repo\jachin-system-pilot")
)

foreach ($dir in $SkillDirs) {
    if (-not (Test-Path (Join-Path $dir "plugin.json"))) {
        Write-Host "跳过（无 plugin.json）: $dir" -ForegroundColor Yellow
        continue
    }
    Write-Host "`n=== $(Split-Path $dir -Leaf) ===" -ForegroundColor Cyan
    Set-Location $dir
    jachin pack
    if ($LASTEXITCODE -ne 0) { throw "jachin pack failed: $dir" }
    jachin publish --nexus $Nexus --visibility PUBLIC --price 0
    if ($LASTEXITCODE -ne 0) { throw "jachin publish failed: $dir" }
}

Write-Host "`n完成。商店: $Nexus/store ；未设 NEXUS_AUTO_APPROVE 时需 /dashboard/admin/review 审核。" -ForegroundColor Green
Set-Location $Root
