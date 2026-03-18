"""
BI 数据持久化层 (D) — DuckDB 存储

职责：
- 接收 A 抓取的 CSV，导入 DuckDB
- 提供查询接口供 C 分析使用
- 管理 raw 数据的历史版本

设计: docs/bi_daily_report/ 中 D 层方案
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from l3_node.mcp_tools.bi.paths import get_bi_duckdb_path, ensure_bi_dirs

logger = logging.getLogger(__name__)


def _sanitize_table_name(slug: str) -> str:
    """将 slug 转为合法 DuckDB 表名"""
    s = re.sub(r"[^\w]", "_", slug.strip())
    return f"bi_{s}" if s else "bi_raw"


def _find_date_column(columns: list[str]) -> str | None:
    """查找业务日期列名（仅明确日期列，避免误删）"""
    for cand in ("日期", "date", "统计日期"):
        for c in columns:
            if cand in c or c == cand:
                return c
    return None


def _q(col: str) -> str:
    """列名加双引号（含特殊字符）"""
    return f'"{col.replace(chr(34), chr(34)+chr(34))}"' if col else "1"


def _get_conn():
    """获取 DuckDB 连接"""
    import duckdb

    ensure_bi_dirs()
    path = get_bi_duckdb_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def ingest_csv(file_path: str | Path, slug: str, captured_at: str | None = None) -> dict[str, Any]:
    """
    将 A 抓取的 CSV 导入 DuckDB。

    Args:
        file_path: CSV 文件路径（A 的 output_path）
        slug: 数据标识（如 daily_ops_summary）
        captured_at: 抓取时间，默认当前时间

    Returns:
        {"status": "success", "slug": slug, "rows": N, "table": "bi_xxx"} 或 {"status": "error", "error": "..."}
    """
    try:
        import duckdb
    except ImportError:
        return {"status": "error", "error": "duckdb 未安装，请执行 pip install duckdb"}

    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "error": f"文件不存在: {file_path}"}
    if path.stat().st_size == 0:
        return {"status": "error", "error": "CSV 文件为空"}

    table_name = _sanitize_table_name(slug)
    path_str = str(path.resolve())

    try:
        conn = _get_conn()
        try:
            # 创建表（含 _ingested_at, _ingested_date），若不存在
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS {table} AS
                SELECT *, current_timestamp::TIMESTAMP AS _ingested_at, current_date AS _ingested_date
                FROM read_csv_auto(?) LIMIT 0
                """.format(table=table_name),
                [path_str],
            )
            # UPSERT：每个日期只存一份，先删后插
            cols = [r[0] for r in conn.execute("DESCRIBE " + table_name).fetchall()]
            date_col = _find_date_column(cols)
            if date_col:
                qd = _q(date_col)
                try:
                    conn.execute(
                        """
                        DELETE FROM {table} WHERE {qd} IN (
                            SELECT {qd} FROM read_csv_auto(?)
                        )
                        """.format(table=table_name, qd=qd),
                        [path_str],
                    )
                except Exception as e:
                    logger.debug("[D] delete before insert skipped: %s", e)
            conn.execute(
                """
                INSERT INTO {table} SELECT *, current_timestamp::TIMESTAMP AS _ingested_at, current_date AS _ingested_date
                FROM read_csv_auto(?)
                """.format(table=table_name),
                [path_str],
            )
            rows = conn.execute("SELECT COUNT(*) FROM read_csv_auto(?)", [path_str]).fetchone()[0]
            conn.close()
            logger.info("[D] ingest: slug=%s rows=%d table=%s (upsert)", slug, rows, table_name)
            return {"status": "success", "slug": slug, "rows": rows, "table": table_name}
        except Exception as e:
            conn.close()
            raise
    except Exception as e:
        logger.exception("[D] ingest failed: %s", e)
        return {"status": "error", "error": str(e)}


def get_table(slug: str, date_from: str | None = None, date_to: str | None = None) -> Any:
    """
    按 slug 和日期范围查询数据，供 C 分析使用。

    Args:
        slug: 数据标识
        date_from: 起始日期 YYYY-MM-DD（可选，过滤 _ingested_date）
        date_to: 结束日期 YYYY-MM-DD（可选）

    Returns:
        pandas.DataFrame 或 None（表不存在时）
    """
    try:
        import duckdb
    except ImportError:
        return None

    table_name = _sanitize_table_name(slug)
    conn = _get_conn()
    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        if table_name not in tables:
            return None
        where = []
        if date_from:
            where.append(f"_ingested_date >= '{date_from}'")
        if date_to:
            where.append(f"_ingested_date <= '{date_to}'")
        sql = f"SELECT * FROM {table_name}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return conn.execute(sql).df()
    finally:
        conn.close()


def query(sql: str, params: list | None = None) -> Any:
    """
    执行任意 SQL 查询（供 C 或高级分析使用）。

    Args:
        sql: SQL 语句
        params: 参数列表（可选）

    Returns:
        duckdb.DuckDBPyRelation
    """
    conn = _get_conn()
    try:
        if params:
            return conn.execute(sql, params)
        return conn.execute(sql)
    finally:
        conn.close()


def list_available_slugs() -> list[str]:
    """返回已导入 DuckDB 的 slug 列表（表名去掉 bi_ 前缀）"""
    try:
        import duckdb
    except ImportError:
        return []

    conn = _get_conn()
    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        return [t[3:] if t.startswith("bi_") else t for t in tables if t.startswith("bi_")]
    finally:
        conn.close()


def list_available_dates(slug: str) -> list[str]:
    """返回某 slug 已有数据的 _ingested_date 列表（去重、排序）"""
    try:
        import duckdb
    except ImportError:
        return []

    table_name = _sanitize_table_name(slug)
    conn = _get_conn()
    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        if table_name not in tables:
            return []
        rows = conn.execute(
            f"SELECT DISTINCT _ingested_date FROM {table_name} ORDER BY _ingested_date"
        ).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        conn.close()
