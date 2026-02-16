# Test Skill Discovery Script
# 测试技能发现脚本

Write-Host "Testing skill discovery..." -ForegroundColor Yellow

# Activate conda environment
$env:CONDA_DEFAULT_ENV = "jachin-dev"
conda activate jachin-dev

# Run Python script to test skill discovery
python -c "
import sys
sys.path.insert(0, '.')
from core.runtime.skill_loader import SkillLoader
from pathlib import Path

loader = SkillLoader()
skills = loader.discover_skills()
print(f'Discovered {len(skills)} skills:')
for skill_id in skills:
    print(f'  - {skill_id}')
    manifest = loader.load_skill_manifest(skill_id)
    if manifest:
        print(f'    Name: {manifest.name}')
        print(f'    Version: {manifest.version}')
    else:
        print(f'    ERROR: Failed to load manifest')
"

Write-Host ""
Write-Host "Test complete!" -ForegroundColor Green
