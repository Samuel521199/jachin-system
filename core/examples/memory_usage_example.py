"""
Memory / 向量存储示例（V2：LanceDB）

旧版独立向量服务适配层已移除；当前使用 LanceDB。业务实现请参考：
- `core.db.l2_memory_lancedb` — L2 向量梦境引擎
- `core.memory_store` — Dream Weaver / memories 表

本脚本仅演示：配置中的 LanceDB 路径、以及能否 `import lancedb` 并列出表名。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from core.config import settings


def _resolve_lancedb_dir() -> Path:
    """与运行时一致的常见路径：显式环境变量优先，否则用 settings.LANCEDB_PATH。"""
    for key in ("JACHIN_LANCEDB_PATH",):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    data = os.environ.get("JACHIN_DATA_DIR")
    if data:
        return Path(data).expanduser() / "lancedb_data"
    return Path(settings.LANCEDB_PATH).expanduser()


async def main() -> None:
    print("=" * 50)
    print("Jachin V2 — LanceDB 向量存储检查")
    print("=" * 50)
    root = _resolve_lancedb_dir()
    print(f"解析路径: {root}")
    print(f"settings.LANCEDB_PATH: {settings.LANCEDB_PATH}")

    try:
        import lancedb
    except ImportError:
        print("\n未安装 lancedb，请: pip install lancedb")
        return

    root.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(root))
    names = db.table_names()
    print(f"\nLanceDB 可连接，表数量: {len(names)}")
    if names:
        print("表名:", ", ".join(names[:20]) + ("..." if len(names) > 20 else ""))


if __name__ == "__main__":
    asyncio.run(main())
