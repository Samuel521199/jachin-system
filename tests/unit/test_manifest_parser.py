"""
Manifest解析器测试
Manifest Parser Tests
"""

import pytest
import tempfile
import yaml
from pathlib import Path
from core.runtime.manifest import ManifestParser, ManifestError, SkillManifest


def test_valid_manifest_yaml():
    """测试有效的YAML Manifest"""
    manifest_data = {
        "name": "test-skill",
        "version": "1.0.0",
        "description": "Test skill",
        "author": "Test Author",
        "license": "MIT",
        "runtime": {
            "type": "docker",
            "image": "test-image:latest",
            "entrypoint": "python main.py"
        },
        "capabilities": [
            {
                "name": "test_action",
                "type": "action",
                "description": "Test action",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"}
            }
        ]
    }

    manifest = ManifestParser.load_from_dict(manifest_data)

    assert manifest.name == "test-skill"
    assert manifest.version == "1.0.0"
    assert manifest.skill_id == "test-skill-1.0.0"
    assert len(manifest.capabilities) == 1
    assert manifest.capabilities[0]["name"] == "test_action"


def test_invalid_manifest_missing_required():
    """测试缺少必需字段的Manifest"""
    manifest_data = {
        "name": "test-skill",
        # 缺少version和runtime
    }

    with pytest.raises(ManifestError):
        ManifestParser.load_from_dict(manifest_data)


def test_manifest_from_file():
    """测试从文件加载Manifest"""
    manifest_data = {
        "name": "test-skill",
        "version": "1.0.0",
        "runtime": {
            "type": "docker"
        },
        "capabilities": [
            {
                "name": "test_action",
                "type": "action"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(manifest_data, f)
        temp_path = f.name

    try:
        manifest = ManifestParser.load_from_file(temp_path)
        assert manifest.name == "test-skill"
    finally:
        Path(temp_path).unlink()


def test_get_capability():
    """测试获取能力"""
    manifest_data = {
        "name": "test-skill",
        "version": "1.0.0",
        "runtime": {"type": "docker"},
        "capabilities": [
            {
                "name": "action1",
                "type": "action"
            },
            {
                "name": "action2",
                "type": "action"
            }
        ]
    }

    manifest = ManifestParser.load_from_dict(manifest_data)

    cap1 = manifest.get_capability("action1")
    assert cap1 is not None
    assert cap1["name"] == "action1"

    cap_none = manifest.get_capability("nonexistent")
    assert cap_none is None
