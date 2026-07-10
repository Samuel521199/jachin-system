"""
技能加载器测试
Skill Loader Tests
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from core.runtime.skill_loader import SkillLoader
from core.runtime.manifest import ManifestParser


def test_discover_skills(tmp_path):
    """测试技能发现"""
    loader = SkillLoader(repo_path=str(tmp_path))

    # 创建测试技能目录
    skill_dir = tmp_path / "test-skill-1.0.0"
    skill_dir.mkdir()

    # 创建manifest文件
    manifest_file = skill_dir / "manifest.yaml"
    manifest_data = {
        "name": "test-skill",
        "version": "1.0.0",
        "runtime": {"type": "docker"},
        "capabilities": [{"name": "test_action", "type": "action"}]
    }
    import yaml
    with open(manifest_file, 'w') as f:
        yaml.dump(manifest_data, f)

    # 发现技能
    skills = loader.discover_skills()

    assert len(skills) == 1
    assert "test-skill-1.0.0" in skills


def test_load_skill_manifest(tmp_path):
    """测试加载技能Manifest"""
    loader = SkillLoader(repo_path=str(tmp_path))

    # 创建测试技能目录
    skill_dir = tmp_path / "test-skill-1.0.0"
    skill_dir.mkdir()

    # 创建manifest文件
    manifest_file = skill_dir / "manifest.yaml"
    manifest_data = {
        "name": "test-skill",
        "version": "1.0.0",
        "runtime": {"type": "docker"},
        "capabilities": [{"name": "test_action", "type": "action"}]
    }
    import yaml
    with open(manifest_file, 'w') as f:
        yaml.dump(manifest_data, f)

    # 加载Manifest
    manifest = loader.load_skill_manifest("test-skill-1.0.0")

    assert manifest is not None
    assert manifest.name == "test-skill"
    assert manifest.version == "1.0.0"


def test_get_skill_path(tmp_path):
    """测试获取技能路径"""
    loader = SkillLoader(repo_path=str(tmp_path))

    # 创建测试技能目录
    skill_dir = tmp_path / "test-skill-1.0.0"
    skill_dir.mkdir()

    # 获取路径
    path = loader.get_skill_path("test-skill-1.0.0")

    assert path is not None
    assert path == skill_dir

    # 不存在的技能
    path_none = loader.get_skill_path("nonexistent-skill")
    assert path_none is None
