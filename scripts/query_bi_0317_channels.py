"""临时脚本：从 bi.duckdb 查 3月17日 所有渠道数据（DAU/DNU）"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l3_node.mcp_tools.bi.paths import get_bi_duckdb_path

# 你指定的 6 个渠道，查 3月17日 DAU
TARGET_CHANNELS = ["unknown", "meta_ads06", "meta_ads05", "meta_ads04", "meta_ads03", "meta_ads01"]

def main():
    p = get_bi_duckdb_path()
    if not p.exists():
        print("DB not found:", p)
        return
    import duckdb
    conn = duckdb.connect(str(p))
    target = "2026-03-17"
    print("=== 3月17日 各渠道 DAU (日期=%s) ===\n" % target)
    # 先查这 6 个渠道在 bi_stats_user_dau 里的 3月17日 DAU
    try:
        rel = conn.execute("""
            SELECT "渠道", "日活（DAU）" AS DAU
            FROM bi_stats_user_dau
            WHERE (CAST("日期" AS VARCHAR) LIKE '2026-03-17%' OR "日期" IS NULL)
              AND "渠道" IN ('unknown','meta_ads06','meta_ads05','meta_ads04','meta_ads03','meta_ads01')
            ORDER BY "渠道"
        """)
        rows = rel.fetchall()
        if rows:
            print("渠道\t\tDAU")
            print("-" * 24)
            for r in rows:
                print("%s\t%s" % (r[0], r[1]))
            print("\n共 %d 个渠道" % len(rows))
        else:
            print("未在库中找到这 6 个渠道的 3月17日 DAU 明细。")
            print("当前 bi_stats_user_dau 中 3月17日 仅有「全部汇总」一行（DAU=530）。")
            print("需要重新抓取「日活统计」并展开首行日期后再导入 DuckDB 才会有各渠道 DAU。\n")
    except Exception as e:
        print("查询出错:", e)
        print("(库中可能暂无这 6 个渠道的明细行)\n")
    print("=== 3月17日 全表渠道数据 (日期=%s) ===\n" % target)
    for slug, name in [
        ("bi_stats_user_dau", "日活统计-渠道DAU"),
        ("bi_stats_user_new", "日新用户统计-渠道DNU"),
    ]:
        try:
            tabs = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
            if slug not in tabs:
                print("[%s] 表不存在，跳过" % slug)
                continue
            cols = [r[0] for r in conn.execute("DESCRIBE " + slug).fetchall()]
            date_col = None
            for c in ["日期", "date", "统计日期"]:
                if c in cols:
                    date_col = c
                    break
            if not date_col:
                date_col = "_ingested_date"
            # 日期列可能是 DATE 或 VARCHAR；展开后子行可能无日期(NULL)，一并查出
            q = '"%s"' % date_col.replace('"', '""')
            rel = conn.execute(
                "SELECT * FROM %s WHERE CAST(%s AS VARCHAR) LIKE '%s%%' OR %s IS NULL ORDER BY %s DESC NULLS LAST"
                % (slug, q, target, q, q)
            )
            rows_all = rel.fetchall()
            col_names = list(rel.columns) if hasattr(rel, "columns") else [d[0] for d in rel.description]
            # 若只有汇总行，再查全表看是否有 NULL 日期的渠道行
            if len(rows_all) <= 1 and date_col != "_ingested_date":
                rel2 = conn.execute("SELECT * FROM %s WHERE %s IS NULL" % (slug, q))
                rows_null = rel2.fetchall()
                if rows_null:
                    col_names = list(rel2.columns) if hasattr(rel2, "columns") else [d[0] for d in rel2.description]
                    rows_all = rows_all + rows_null
            rows = rows_all
            print("[%s] %s: %d 行" % (slug, name, len(rows)))
            if rows:
                for i, row in enumerate(rows[:25]):
                    print("  ", dict(zip(col_names, row)))
                if len(rows) > 25:
                    print("  ... 共 %d 行" % len(rows))
            print()
        except Exception as e:
            print("[%s] 错误: %s\n" % (slug, e))
    conn.close()

if __name__ == "__main__":
    main()
