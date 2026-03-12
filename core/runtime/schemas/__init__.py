"""
技能运行时Schema模块
Skill Runtime Schema Module
"""

import json
from pathlib import Path

# 加载Manifest Schema
_SCHEMA_PATH = Path(__file__).parent / "manifest_schema.json"

with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
    MANIFEST_SCHEMA = json.load(f)

__all__ = ["MANIFEST_SCHEMA"]
