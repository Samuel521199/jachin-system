#!/usr/bin/env python3
"""
Jachin Nexus V2 - 权限结构化迁移脚本

将 sub_accounts.permissions_json 扁平数据迁移至 sub_account_permissions 表。
可独立运行，也可由 core.db.schema.init_all 自动执行。

用法:
    python -m scripts.migrate_permissions_to_structured
    或
    cd core && python -c "from db.schema import init_all; from db import get_connection; init_all(get_connection())"
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根在 path 中
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from core.db import get_connection
from core.db.schema import _migrate_permissions_to_structured


def main() -> int:
    conn = get_connection()
    try:
        _migrate_permissions_to_structured(conn)
        print("OK: 权限迁移完成")
        return 0
    except Exception as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
