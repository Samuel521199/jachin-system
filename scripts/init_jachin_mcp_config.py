#!/usr/bin/env python3
"""
将项目 config/skills/ 和 config/mcps/ 模板初始化到 ~/.jachin/config/

规范: .cursor/rules/075-config-root-and-cloud-sync(1).mdc
用法: python scripts/init_jachin_mcp_config.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.jachin_config import get_config_root


def _copy_tree(src: Path, dst: Path, name: str) -> None:
    if not src.exists():
        print(f"[{name}] 源目录不存在: {src}")
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            # .example 文件复制为目标文件名（config.yaml.example -> config.yaml）
            dst_name = rel.name.replace(".example", "") if ".example" in rel.name else rel.name
            dst_file = dst / rel.parent / dst_name
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if not dst_file.exists():
                shutil.copy2(item, dst_file)
                print(f"已写入: {dst_file}")
            else:
                print(f"已存在，跳过: {dst_file}")


def main() -> int:
    cfg_root = get_config_root()
    _copy_tree(_root / "config" / "skills", cfg_root / "skills", "skills")
    _copy_tree(_root / "config" / "mcps", cfg_root / "mcps", "mcps")
    print(f"\n配置根: {cfg_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
