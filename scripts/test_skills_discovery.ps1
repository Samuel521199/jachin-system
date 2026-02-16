# UTF-8 输出，避免中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$r = Invoke-RestMethod -Uri "http://localhost:18888/api/v3/skills/debug/discovery" -Method GET
Write-Host "discovered_count: $($r.discovered_count)"
Write-Host "discovered_skills: $($r.discovered_skills -join ', ')"
Write-Host ""
Write-Host "skill_details (name):"
$r.skill_details | ForEach-Object { Write-Host "  - $($_.skill_id): $($_.name)" }
