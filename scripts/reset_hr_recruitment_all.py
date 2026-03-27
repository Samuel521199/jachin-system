#!/usr/bin/env python3
"""
一键清空招聘相关本地数据：岗位目录（pending/processed/result/jd）、调度状态、
指针、审计、HR workflow 状态、Lark 会话、透析输出目录、Boss 收网卷（可选保留 bi_data）。

逻辑实现见 l3_node.hr_workspace_full_reset（与 Lark「清除全部岗位记忆」硬指令共用）。

**请先停止** L3 / 招聘调度 / Boss 浏览器自动化，避免文件锁导致删除不完整。

用法:
  python scripts/reset_hr_recruitment_all.py
  python scripts/reset_hr_recruitment_all.py --dry-run
  python scripts/reset_hr_recruitment_all.py --keep-lark-chat --keep-lark-sessions

环境变量与插件一致: JACHIN_HOME, JACHIN_HR_DATA_ROOT, JACHIN_HR_ANALYSIS_OUTPUT, JACHIN_HR_RESUME_ROOT
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from l3_node.hr_workspace_full_reset import (  # noqa: E402
    hr_data_root,
    run_full_hr_recruitment_reset_round,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="清空招聘岗位本地数据与相关记忆")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的操作")
    ap.add_argument(
        "--keep-lark-chat",
        action="store_true",
        help="指针里保留 lark_chat_id（默认清空，彻底重来）",
    )
    ap.add_argument(
        "--keep-lark-sessions",
        action="store_true",
        help="保留 ~/.jachin/l3_lark_sessions.json（默认清空飞书多轮上下文）",
    )
    ap.add_argument(
        "--no-clear-client-volumes",
        action="store_true",
        help="不删 ~/.jachin/client_volumes 下卷（默认会删，但保留 bi_data）",
    )
    ap.add_argument(
        "--clear-hr-resume-root",
        action="store_true",
        help="同时删除 JACHIN_HR_RESUME_ROOT（默认 ~/.jachin/workspace/hr_resumes）",
    )
    ap.add_argument(
        "--no-clear-hr-analysis",
        action="store_true",
        help="不删除透析镜输出根目录（默认删除）",
    )
    ap.add_argument(
        "--max-rounds",
        type=int,
        default=5,
        help="删除失败时重试轮数（文件锁等）",
    )
    args = ap.parse_args()

    clear_client_volumes = not args.no_clear_client_volumes
    clear_hr_analysis = not args.no_clear_hr_analysis
    max_r = max(1, args.max_rounds)

    for round_i in range(1, max_r + 1):
        print(f"======== 第 {round_i} 轮 ========")
        run_full_hr_recruitment_reset_round(
            dry_run=args.dry_run,
            keep_lark_chat=args.keep_lark_chat,
            keep_lark_sessions=args.keep_lark_sessions,
            clear_client_volumes=clear_client_volumes,
            clear_hr_resume_root=args.clear_hr_resume_root,
            clear_hr_analysis=clear_hr_analysis,
            emit=print,
        )
        root = hr_data_root()
        leftover_dirs: list[str] = []
        leftover_files: list[str] = []
        if root.is_dir():
            leftover_dirs = [
                p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
            ]
            leftover_files = [
                p.name for p in root.iterdir() if p.is_file() and not p.name.startswith(".")
            ]
        if not leftover_dirs and not leftover_files:
            print()
            print("======== 完成：hr_recruitment 根下已无岗位子目录与松散文件 ========")
            return 0
        if args.dry_run:
            print()
            print("======== dry-run 结束 ========")
            return 0
        if round_i < max_r:
            print(f"  … 仍有残留: 目录={leftover_dirs} 文件={leftover_files}，2s 后重试")
            time.sleep(2)
        else:
            print()
            print(f"======== 仍有残留，请关闭占用进程后手工删: {root} ========")
            print(f"  目录: {leftover_dirs}")
            print(f"  文件: {leftover_files}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
