#!/usr/bin/env python3
"""
重置 L2 网关管理员密码为 admin/admin123
用法: python scripts/reset_admin_password.py  （需在项目根目录执行）
依赖: pip install passlib[bcrypt]
"""
import json
import sys
from pathlib import Path

# 确保项目根目录在 path 中
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
import secrets
from pathlib import Path

CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"


def main():
    try:
        from core.db import get_connection
        import bcrypt
    except ImportError as e:
        print("请先安装依赖: pip install bcrypt")
        print("或激活 jachin-layer2 环境: conda activate jachin-layer2")
        raise SystemExit(1) from e

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM gateway_admins WHERE username = 'admin' LIMIT 1"
        ).fetchone()
        password = "admin123"
        pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        if not row:
            cfg = {}
            if CONFIG_PATH.exists():
                try:
                    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except Exception:
                    pass
            main_user_id = cfg.get("l1_user_id") or cfg.get("instance_id") or f"gw-admin-{secrets.token_hex(4)}"
            conn.execute(
                """
                INSERT INTO gateway_admins (id, username, password_hash, main_user_id, role)
                VALUES (?, 'admin', ?, ?, 'admin')
                """,
                (f"gw-admin-{secrets.token_hex(4)}", pw_hash, main_user_id),
            )
            conn.commit()
            print("已创建 admin 账号，密码: admin123")
        else:
            conn.execute(
                "UPDATE gateway_admins SET password_hash = ? WHERE username = 'admin'",
                (pw_hash,),
            )
            conn.commit()
            print("已重置 admin 密码为: admin123")
        print("登录: http://localhost:18888/admin")
    except Exception as e:
        print(f"错误: {e}")
        raise SystemExit(1) from e
    finally:
        conn.close()


if __name__ == "__main__":
    main()
