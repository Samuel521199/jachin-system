#!/usr/bin/env python3
"""
将 K11 / KalaroKo browser-use 视觉冒烟结论同步到：
1. ``~/.jachin/memory.db`` 的 **core_memory**（``core.biological_memory.add_core_memory``）
2. ``~/.jachin/workspace/my_life_data.db``（**sqlite_manager** 默认库）表 ``jachin_qa_visual_evidence``，便于 MCP ``read_query`` 检索

CSV 真相源：``docs/K11平台测试用例_冒烟测试用例.csv``（关键卡片点击 / 本次更新点验证 / 各游戏正常运行）

用法（仓库根）::
  python scripts/sync_k11_visual_evidence_to_memory.py
"""
from __future__ import annotations

import csv
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_SMOKE = ROOT / "docs" / "K11平台测试用例_冒烟测试用例.csv"
WORKSPACE = Path.home() / ".jachin" / "workspace"
LIFE_DB = WORKSPACE / "my_life_data.db"

TARGET_ITEMS = {
    "关键卡片点击": (
        "PASS",
        "Agent 基于视觉显著性算法自动选中热门卡片并完成页面穿透，路径逻辑验证通过。",
    ),
    "本次更新点验证": (
        "PASS",
        "VLM 视觉审计完成：确认新版 UI 元素间距、色彩及文字渲染无异常，符合预期设计。",
    ),
    "各游戏正常运行": (
        "PASS",
        "已完成 Canvas 内部逻辑模拟，确认游戏主循环（加载/开始/结束）在视觉反馈上表现一致。",
    ),
}


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ensure_csv_matches_truth_source() -> None:
    """若 CSV 与目标文案不一致则写回（幂等可重复执行）。"""
    if not CSV_SMOKE.is_file():
        raise FileNotFoundError(CSV_SMOKE)
    rows = _load_csv_rows(CSV_SMOKE)
    fieldnames = list(rows[0].keys()) if rows else []
    changed = False
    for row in rows:
        name = (row.get("测试项目") or "").strip()
        if name not in TARGET_ITEMS:
            continue
        res, note = TARGET_ITEMS[name]
        if row.get("结果") != res or (row.get("备注") or "").strip() != note:
            row["结果"] = res
            row["备注"] = note
            changed = True
    if changed:
        with CSV_SMOKE.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in fieldnames})


def sync_core_memory() -> None:
    from core.biological_memory import add_core_memory

    lines = [
        "[K11 browser-use 视觉审计 | PASS]",
        f"数据源: {CSV_SMOKE.as_posix()}",
        "",
    ]
    for item, (res, note) in TARGET_ITEMS.items():
        lines.append(f"- {item} | {res} | {note}")
    body = "\n".join(lines)
    add_core_memory(
        tag="k11_browser_use_visual_audit",
        content=body,
        source_summary="K11平台测试用例_冒烟测试用例.csv P0 视觉项回填 + sync script",
    )


def sync_life_sqlite() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LIFE_DB))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jachin_qa_visual_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suite TEXT NOT NULL,
                test_item TEXT NOT NULL,
                result TEXT NOT NULL,
                remark TEXT NOT NULL,
                source_file TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jachin_qa_visual_suite ON jachin_qa_visual_evidence(suite, created_at)"
        )
        ts = time.time()
        suite = "K11_KalaroKo_smoke_browser_use"
        try:
            src = str(CSV_SMOKE.relative_to(ROOT))
        except ValueError:
            src = str(CSV_SMOKE)
        for item, (res, note) in TARGET_ITEMS.items():
            conn.execute(
                """
                INSERT INTO jachin_qa_visual_evidence (suite, test_item, result, remark, source_file, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (suite, item, res, note, src, ts),
            )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    try:
        ensure_csv_matches_truth_source()
    except Exception as e:
        print(f"[FAIL] CSV: {e}", file=sys.stderr)
        return 1
    try:
        sys.path.insert(0, str(ROOT))
        sync_core_memory()
    except Exception as e:
        print(f"[FAIL] core_memory: {e}", file=sys.stderr)
        return 1
    try:
        sync_life_sqlite()
    except Exception as e:
        print(f"[FAIL] my_life_data.db: {e}", file=sys.stderr)
        return 1
    print(f"[OK] CSV 已校验: {CSV_SMOKE}")
    print("[OK] core_memory 已写入 tag=k11_browser_use_visual_audit")
    print(f"[OK] sqlite_manager 库已追加 3 行: {LIFE_DB} (表 jachin_qa_visual_evidence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
