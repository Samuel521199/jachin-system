# Test Skill Reload Script
# 测试技能动态加载脚本

Write-Host "Testing skill reload functionality..." -ForegroundColor Yellow
Write-Host ""

# Test reload all skills
Write-Host "[1] Reloading all skills..." -ForegroundColor Cyan
try {
    $response = Invoke-RestMethod -Uri "http://localhost:18888/api/v3/skills/reload" -Method POST -ContentType "application/json"
    Write-Host "Success!" -ForegroundColor Green
    Write-Host "  Total discovered: $($response.total_discovered)" -ForegroundColor Gray
    Write-Host "  Newly loaded: $($response.newly_loaded)" -ForegroundColor Gray
    Write-Host "  Updated: $($response.updated)" -ForegroundColor Gray
    Write-Host "  Errors: $($response.errors)" -ForegroundColor $(if ($response.errors -gt 0) { "Yellow" } else { "Gray" })
    if ($response.error_details) {
        Write-Host "  Error details:" -ForegroundColor Red
        $response.error_details | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    }
} catch {
    Write-Host "Failed: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""

# Test list skills
Write-Host "[2] Listing all skills..." -ForegroundColor Cyan
try {
    $skills = Invoke-RestMethod -Uri "http://localhost:18888/api/v3/skills" -Method GET
    Write-Host "Found $($skills.Count) skills:" -ForegroundColor Green
    foreach ($skill in $skills) {
        Write-Host "  - $($skill.skill_id) ($($skill.name))" -ForegroundColor Gray
        Write-Host "    Version: $($skill.version), Status: $($skill.status)" -ForegroundColor DarkGray
        Write-Host "    Capabilities: $($skill.capabilities.Count)" -ForegroundColor DarkGray
    }
} catch {
    Write-Host "Failed: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "Test complete!" -ForegroundColor Green
