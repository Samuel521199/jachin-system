#!/usr/bin/env python3
"""
根据 mcp-pyautogui（物理层）闭环结论，更新冒烟 CSV「各游戏正常运行」的 结果/备注，
并写入 ``~/.jachin/workspace/my_life_data.db`` 表 ``jachin_qa_visual_evidence``（与 sqlite_manager 同库）。

用法（仓库根）::

  python scripts/sync_k11_pyautogui_physical_evidence.py --result pass
  python scripts/sync_k11_pyautogui_physical_evidence.py --result fail

也可由环境变量覆盖（便于 CI）::

  set K11_PYAUTO_GAME_ROUND_RESULT=pass
  python scripts/sync_k11_pyautogui_physical_evidence.py
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_SMOKE = ROOT / "docs" / "K11平台测试用例_冒烟测试用例.csv"
WORKSPACE = Path.home() / ".jachin" / "workspace"
LIFE_DB = WORKSPACE / "my_life_data.db"

ITEM = "各游戏正常运行"
REMARK_PASS = (
    "【物理审计】图像识别成功匹配 Canvas 特征点，物理点击响应正常，闭环逻辑通过。"
)
REMARK_FAIL = "【物理报警】图像匹配失败，可能存在 Canvas 渲染异常或 UI 资源未加载。"
SUITE = "K11_KalaroKo_smoke_pyautogui_physical"


def _load_csv_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, fieldnames


def update_csv(path: Path, *, passed: bool) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows, fieldnames = _load_csv_rows(path)
    if not fieldnames:
        raise ValueError("CSV 无表头")
    res = "PASS" if passed else "FAIL"
    note = REMARK_PASS if passed else REMARK_FAIL
    found = False
    for row in rows:
        if (row.get("测试项目") or "").strip() == ITEM:
            row["结果"] = res
            row["备注"] = note
            found = True
            break
    if not found:
        raise KeyError(f'未找到测试项目行: "{ITEM}"')
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def append_sqlite(*, passed: bool) -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    res = "PASS" if passed else "FAIL"
    note = REMARK_PASS if passed else REMARK_FAIL
    try:
        src = str(CSV_SMOKE.relative_to(ROOT))
    except ValueError:
        src = str(CSV_SMOKE)
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
        conn.execute(
            """
            INSERT INTO jachin_qa_visual_evidence (suite, test_item, result, remark, source_file, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (SUITE, ITEM, res, note, src, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="同步 K11 pyautogui 物理冒烟结论到 CSV + my_life_data.db")
    ap.add_argument(
        "--result",
        choices=("pass", "fail"),
        default=None,
        help="物理闭环是否通过（也可用环境变量 K11_PYAUTO_GAME_ROUND_RESULT=pass|fail）",
    )
    args = ap.parse_args()
    raw = (args.result or os.environ.get("K11_PYAUTO_GAME_ROUND_RESULT") or "").strip().lower()
    if raw in ("1", "true", "yes", "pass", "ok"):
        passed = True
    elif raw in ("0", "false", "no", "fail", "failed"):
        passed = False
    else:
        print(
            "请指定结论: --result pass|fail 或设置 K11_PYAUTO_GAME_ROUND_RESULT",
            file=sys.stderr,
        )
        return 2
    try:
        update_csv(CSV_SMOKE, passed=passed)
    except Exception as e:
        print(f"[FAIL] CSV: {e}", file=sys.stderr)
        return 1
    try:
        append_sqlite(passed=passed)
    except Exception as e:
        print(f"[FAIL] my_life_data.db: {e}", file=sys.stderr)
        return 1
    print(f"[OK] 已更新: {CSV_SMOKE} | {ITEM} -> {'PASS' if passed else 'FAIL'}")
    print(f"[OK] sqlite 证据已追加: {LIFE_DB} suite={SUITE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
