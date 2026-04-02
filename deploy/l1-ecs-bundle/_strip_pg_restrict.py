"""从 pg_dump 输出去掉 PG16 \\restrict / \\unrestrict，并加说明头。"""
from pathlib import Path

HEADER = """-- =============================================================================
-- Jachin L1 (Nexus) 全量表结构（与 cloud/nexus drizzle-kit push + init-store 后一致）
-- 服务器仅需：PostgreSQL + psql（或 docker run postgres psql），无需 Node / 无需仓库代码
-- 使用顺序：
--   1) psql ... -f l1_reset_public_schema.sql
--   2) psql ... -v ON_ERROR_STOP=1 -f l1_nexus_full_schema.sql
-- 再生成本文件（开发机在仓库根目录，需 Docker）：
--   见 README.txt「再生成全量 SQL」
-- =============================================================================

"""

p = Path(__file__).resolve().parent / "l1_nexus_full_schema.sql"
lines = p.read_text(encoding="utf-8").splitlines()
body = [
    ln
    for ln in lines
    if not (ln.startswith("\\restrict") or ln.startswith("\\unrestrict"))
]
p.write_text(HEADER + "\n".join(body) + "\n", encoding="utf-8")
print("stripped", len(lines) - len(body), "lines")
