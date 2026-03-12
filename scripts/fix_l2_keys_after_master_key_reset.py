#!/usr/bin/env python3
"""
修复 L2 API Key 无法解密问题

当 JACHIN_L2_MASTER_KEY 未设置时，L2 每次启动使用临时 Key 加密存储。
重启后临时 Key 丢失，无法解密已有 Key，导致 L3 收不到 API Key、聊天不可用。

使用步骤：
1. 在项目根 .env 中添加（或生成）：
   python -c "import secrets; print('JACHIN_L2_MASTER_KEY=' + secrets.token_hex(32))"
2. 运行本脚本清除无法解密的旧 Key：
   python scripts/fix_l2_keys_after_master_key_reset.py
3. 重启 L2，sync_api_keys_from_env 会从 DASHSCOPE_API_KEY 等重新同步
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# 加载 .env
for _p in [_root / ".env", Path.cwd() / ".env"]:
    if _p.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_p, encoding="utf-8")
            break
        except ImportError:
            break

def main() -> None:
    if not os.environ.get("JACHIN_L2_MASTER_KEY"):
        print("请先在 .env 中设置 JACHIN_L2_MASTER_KEY，例如：")
        print('  python -c "import secrets; print(\'JACHIN_L2_MASTER_KEY=\' + secrets.token_hex(32))"')
        print("将输出追加到 .env 后重新运行本脚本。")
        sys.exit(1)

    from core.db import get_connection

    conn = get_connection()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM api_keys_vault")
        n = cur.fetchone()[0]
        conn.execute("DELETE FROM api_keys_vault")
        conn.commit()
        print(f"已清除 {n} 条无法解密的 API Key。")
        print("请重启 L2，将从环境变量重新同步 DASHSCOPE_API_KEY 等。")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
