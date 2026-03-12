#!/usr/bin/env python
"""Test script to verify skill discovery and loading"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.runtime.skill_loader import SkillLoader
from core.runtime.manifest import ManifestParser, ManifestError

def test_skill_discovery():
    """Test skill discovery"""
    print("=" * 60)
    print("Testing Skill Discovery")
    print("=" * 60)
    
    loader = SkillLoader()
    skills = loader.discover_skills()
    
    print(f"\nDiscovered {len(skills)} skills:")
    for skill_id in skills:
        print(f"  - {skill_id}")
    
    print("\n" + "=" * 60)
    print("Testing Manifest Loading")
    print("=" * 60)
    
    for skill_id in skills:
        print(f"\nLoading manifest for: {skill_id}")
        try:
            manifest = loader.load_skill_manifest(skill_id)
            if manifest:
                print(f"  [OK] Skill ID: {manifest.skill_id}")
                print(f"  [OK] Name: {manifest.name}")
                print(f"  [OK] Version: {manifest.version}")
                print(f"  [OK] Runtime Type: {manifest.runtime_type}")
                print(f"  [OK] Capabilities: {len(manifest.capabilities)}")
            else:
                print(f"  [X] Failed to load manifest")
        except Exception as e:
            print(f"  [X] Error: {e}")

if __name__ == "__main__":
    test_skill_discovery()
