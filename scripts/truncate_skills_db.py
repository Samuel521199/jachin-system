"""临时脚本：清空 skills / skill_capabilities 表，便于重新注册技能（修复中文乱码）"""
import os
import sys

# 项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    from core.config import settings
    url = settings.DATABASE_URL
    if not url or not url.startswith("postgresql://"):
        print("ERROR: DATABASE_URL not found in .env or environment")
        sys.exit(1)
    # 使用 psycopg2 同步连接（避免 async）
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)
    # 解析 postgresql://user:pass@host:port/dbname
    from urllib.parse import urlparse
    u = urlparse(url)
    host = u.hostname or "localhost"
    port = u.port or 5432
    dbname = (u.path or "/jachin_brain").lstrip("/") or "jachin_brain"
    user = u.username or "jachin"
    password = u.password or ""
    conn = psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM skill_capabilities;")
    cur.execute("DELETE FROM skills;")
    print("OK: skill_capabilities and skills cleared.")
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
