# Debug Skill Discovery Script
# 调试技能发现脚本

Write-Host "Debugging skill discovery..." -ForegroundColor Yellow
Write-Host ""

# Check skills_repo path
$repoPath = "skills_repo"
Write-Host "[1] Checking skills_repo path: $repoPath" -ForegroundColor Cyan
if (Test-Path $repoPath) {
    Write-Host "  [OK] Path exists" -ForegroundColor Green
    $bundledPath = Join-Path $repoPath "_bundled"
    if (Test-Path $bundledPath) {
        Write-Host "  [OK] _bundled directory exists" -ForegroundColor Green
        $bundledDirs = Get-ChildItem $bundledPath -Directory
        Write-Host "  Found $($bundledDirs.Count) skill directories:" -ForegroundColor Gray
        foreach ($dir in $bundledDirs) {
            Write-Host "    - $($dir.Name)" -ForegroundColor DarkGray
            $manifestPath = Join-Path $dir.FullName "manifest.yaml"
            if (Test-Path $manifestPath) {
                Write-Host "      [OK] manifest.yaml exists" -ForegroundColor Green
                # Try to read and parse
                try {
                    $content = Get-Content $manifestPath -Raw -Encoding UTF8
                    Write-Host "      Content preview:" -ForegroundColor DarkGray
                    $content -split "`n" | Select-Object -First 5 | ForEach-Object {
                        Write-Host "        $_" -ForegroundColor DarkGray
                    }
                } catch {
                    Write-Host "      [ERROR] Failed to read: $_" -ForegroundColor Red
                }
            } else {
                Write-Host "      [WARNING] manifest.yaml not found" -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "  [ERROR] _bundled directory not found" -ForegroundColor Red
    }
} else {
    Write-Host "  [ERROR] skills_repo path not found" -ForegroundColor Red
}

Write-Host ""
Write-Host "[2] Testing Python skill discovery..." -ForegroundColor Cyan
$pythonScript = @"
import sys
from pathlib import Path
sys.path.insert(0, '.')
from core.runtime.skill_loader import SkillLoader

loader = SkillLoader()
print(f'Repo path: {loader.repo_path}')
print(f'Path exists: {loader.repo_path.exists()}')
skills = loader.discover_skills()
print(f'Discovered {len(skills)} skills: {skills}')
"@

try {
    $output = python -c $pythonScript 2>&1
    Write-Host $output
} catch {
    Write-Host "Failed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "Debug complete!" -ForegroundColor Green
