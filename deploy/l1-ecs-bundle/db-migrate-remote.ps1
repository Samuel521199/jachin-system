# 在「本机」对远端 ECS 上的 PostgreSQL 执行 Drizzle 迁移（需安全组放行 5432 或 SSH 隧道）
# 用法：在仓库根目录  .\deploy\l1-ecs-bundle\db-migrate-remote.ps1
$ErrorActionPreference = "Stop"
$Here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$RepoRoot = (Resolve-Path (Join-Path $Here "..\..")).Path

$env:DATABASE_URL = "postgres://jachin:postgres@47.86.39.173:5432/jachin_nexus"
Set-Location (Join-Path $RepoRoot "cloud\nexus")

Write-Host "[db-migrate-remote] DATABASE_URL -> 47.86.39.173:5432/jachin_nexus" -ForegroundColor Cyan
npm ci
npm run db:migrate
npm run db:init-store
Write-Host "[OK] 迁移完成" -ForegroundColor Green
