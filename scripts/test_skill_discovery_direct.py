#!/usr/bin/env python
"""Direct test of skill discovery"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

print("=" * 60)
print("Testing Skill Discovery")
print("=" * 60)

# Check paths
repo_path = Path("skills_repo")
print(f"\n[1] Skills repo path: {repo_path.absolute()}")
print(f"    Exists: {repo_path.exists()}")

bundled_path = repo_path / "_bundled"
print(f"\n[2] Bundled path: {bundled_path.absolute()}")
print(f"    Exists: {bundled_path.exists()}")

if bundled_path.exists():
    skill_dirs = [d for d in bundled_path.iterdir() if d.is_dir()]
    print(f"    Found {len(skill_dirs)} skill directories:")
    for skill_dir in skill_dirs:
        print(f"      - {skill_dir.name}")
        manifest_file = skill_dir / "manifest.yaml"
        print(f"        manifest.yaml exists: {manifest_file.exists()}")
        if manifest_file.exists():
            try:
                content = manifest_file.read_text(encoding='utf-8')
                print(f"        File size: {len(content)} bytes")
                # Try to parse
                import yaml
                data = yaml.safe_load(content)
                print(f"        Parsed successfully")
                print(f"        ID field: {data.get('id', 'NOT FOUND')}")
                print(f"        Name: {data.get('name', 'NOT FOUND')}")
                print(f"        Runtime type: {data.get('runtime', {}).get('type', 'NOT FOUND')}")
            except Exception as e:
                print(f"        Parse error: {e}")

# Test SkillLoader
print("\n[3] Testing SkillLoader.discover_skills()...")
try:
    from core.runtime.skill_loader import SkillLoader
    
    loader = SkillLoader()
    print(f"    Loader repo path: {loader.repo_path.absolute()}")
    print(f"    Path exists: {loader.repo_path.exists()}")
    
    skills = loader.discover_skills()
    print(f"    Discovered {len(skills)} skills: {skills}")
    
    if len(skills) == 0:
        print("\n    [DEBUG] Checking why no skills found...")
        if not loader.repo_path.exists():
            print(f"    ERROR: Repo path does not exist: {loader.repo_path}")
        else:
            bundled = loader.repo_path / "_bundled"
            if not bundled.exists():
                print(f"    ERROR: _bundled directory does not exist: {bundled}")
            else:
                dirs = list(bundled.iterdir())
                print(f"    Found {len(dirs)} items in _bundled:")
                for item in dirs:
                    print(f"      - {item.name} (is_dir: {item.is_dir()})")
                    if item.is_dir():
                        manifest = item / "manifest.yaml"
                        print(f"        manifest.yaml: {manifest.exists()}")
                        if manifest.exists():
                            try:
                                from core.runtime.manifest import ManifestParser
                                m = ManifestParser.load_from_file(str(manifest))
                                print(f"        Parsed skill_id: {m.skill_id}")
                            except Exception as e:
                                print(f"        Parse failed: {e}")
    
except Exception as e:
    print(f"    ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("Test complete!")
print("=" * 60)
