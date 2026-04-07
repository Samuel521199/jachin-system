"""
BI 数据持久化层 (D) — DuckDB 存储

职责：
- 接收 A 抓取的 CSV，导入 DuckDB
- 提供查询接口供 C 分析使用
- 管理 raw 数据的历史版本

设计: docs/bi_daily_report/ 中 D 层方案
"""
from __future__ import annotations

import csv
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_duckdb_path, ensure_bi_dirs

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


_THOUSAND_SEP_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def _normalize_csv_thousand_separators(path: Path) -> tuple[str, bool]:
    """
    BI 表格抓取常带千分位逗号（如 1,697），DuckDB 写入 INT 列会失败。
    返回 (供 read_csv_auto 使用的路径, 是否为临时文件需删除)。
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except UnicodeDecodeError:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    if not rows:
        return str(path.resolve()), False

    def norm_cell(cell: str) -> tuple[str, bool]:
        s = (cell or "").strip()
        if not s or "%" in s:
            return cell, False
        if any(c.isalpha() for c in s):
            return cell, False
        if "," not in s or not _THOUSAND_SEP_NUM_RE.match(s):
            return cell, False
        return s.replace(",", ""), True

    changed = False
    out_rows: list[list[str]] = []
    for row in rows:
        new_r = []
        for c in row:
            nc, ch = norm_cell(c)
            if ch:
                changed = True
            new_r.append(nc)
        out_rows.append(new_r)
    if not changed:
        return str(path.resolve()), False

    fd, tmp = tempfile.mkstemp(suffix=".normalized.csv", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as wf:
            csv.writer(wf, lineterminator="\n").writerows(out_rows)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return tmp, True


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
    norm_path, norm_is_tmp = _normalize_csv_thousand_separators(path)
    path_str = norm_path

    def _do_ingest(conn: Any, csv_path: str, *, drop_first: bool) -> int:
        if drop_first:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS {table} AS
            SELECT *, current_timestamp::TIMESTAMP AS _ingested_at, current_date AS _ingested_date
            FROM read_csv_auto(?) LIMIT 0
            """.format(table=table_name),
            [csv_path],
        )
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
                    [csv_path],
                )
            except Exception as e:
                logger.debug("[D] delete before insert skipped: %s", e)
        conn.execute(
            """
            INSERT INTO {table} SELECT *, current_timestamp::TIMESTAMP AS _ingested_at, current_date AS _ingested_date
            FROM read_csv_auto(?)
            """.format(table=table_name),
            [csv_path],
        )
        return conn.execute("SELECT COUNT(*) FROM read_csv_auto(?)", [csv_path]).fetchone()[0]

    try:
        conn = _get_conn()
        try:
            rows = _do_ingest(conn, path_str, drop_first=False)
        except Exception as e1:
            err = str(e1)
            retry_drop = "columns but" in err and "values" in err
            if retry_drop:
                logger.warning("[D] ingest column mismatch, drop %s and retry: %s", table_name, err)
                try:
                    rows = _do_ingest(conn, path_str, drop_first=True)
                except Exception as e2:
                    conn.close()
                    raise e2 from e1
            else:
                conn.close()
                raise e1
        conn.close()
        logger.info("[D] ingest: slug=%s rows=%d table=%s (upsert)", slug, rows, table_name)
        out = {"status": "success", "slug": slug, "rows": rows, "table": table_name}
    except Exception as e:
        logger.exception("[D] ingest failed: %s", e)
        out = {"status": "error", "error": str(e)}
    finally:
        if norm_is_tmp:
            try:
                os.unlink(norm_path)
            except OSError:
                pass

    return out


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
