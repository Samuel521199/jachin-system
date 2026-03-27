#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 HR 招聘流程读取 jd.json（workflow 指针或显式路径），校验 Boss 下拉行与城市后，
调用与 MCP「atom_greet_recommend_boss」相同的本地实现，最多对 N 人打招呼；每人一条日志。

前置：
  - Chrome 以远程调试启动（默认 http://127.0.0.1:9222）
  - 已登录 Boss 直聘
  - ~/.jachin/memory/hr_recruitment_workflow_pointer.json 含 jd_config_path，
    或传 --jd-config 指向 data/{职位}/jd.json（实际路径在 ~/.jachin/workspace/hr_recruitment/...）

示例：
  python scripts/test_greet_mcp_from_hr_flow.py --max-greet 5
  python scripts/test_greet_mcp_from_hr_flow.py --jd-config "D:/.../hr_recruitment/Java开发工程师/jd.json"
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from tools.atom_greet_recommend_boss import atom_greet_recommend_boss  # noqa: E402
from tools.atom_post_job_boss import get_jd_select, load_jd_config  # noqa: E402
from tools.boss_utils import _jd_select_line_matches, canonicalize_boss_job_select  # noqa: E402
from tools.hr_data_paths import PLUGIN_DATA_ROOT  # noqa: E402


def _resolve_jd_config_path(explicit: str) -> str:
    p = (explicit or "").strip()
    if p:
        path = Path(p)
        if not path.is_absolute():
            cand = Path.cwd() / p
            if cand.exists():
                return str(cand.resolve())
            cand2 = REPO_ROOT / p
            if cand2.exists():
                return str(cand2.resolve())
        if path.exists():
            return str(path.resolve())
        return p

    try:
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        ptr = get_hr_recruitment_workflow_pointer()
        jdp = (ptr.get("jd_config_path") or "").strip()
        if jdp and Path(jdp).exists():
            logging.info("使用 HR workflow 指针中的 jd_config_path: %s", jdp)
            return jdp
    except Exception as e:
        logging.debug("读取 workflow 指针失败: %s", e)

    # 在数据根下按 jd.json 扫描，用 Boss 行匹配（与 --expect-boss-line 在校验阶段对齐）
    return ""


def _find_jd_by_boss_line(expect_canon: str) -> str:
    root = PLUGIN_DATA_ROOT
    if not root.is_dir():
        return ""
    for jd_path in sorted(root.glob("*/jd.json")):
        try:
            jd = json.loads(jd_path.read_text(encoding="utf-8"))
            sel = get_jd_select(jd)
            if _jd_select_line_matches(expect_canon, sel):
                logging.info("在数据根下匹配到 jd.json: %s → jd_select=%r", jd_path, sel)
                return str(jd_path.resolve())
        except Exception:
            continue
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="HR 流程 jd.json → 推荐牛人打招呼（与 MCP 本地工具同路径）")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome CDP 地址")
    ap.add_argument("--jd-config", default="", help="jd.json 绝对或相对路径（优先于 workflow 指针）")
    ap.add_argument("--max-greet", type=int, default=5, help="本轮最多成功打招呼人数（默认 5）")
    ap.add_argument(
        "--expect-boss-line",
        default="Java 开发工程师 _杭州 20-35K",
        help="与 Boss「全部职位」下拉一致的期望行（会与 get_jd_select(jd) 匹配）",
    )
    ap.add_argument("--expect-city", default="杭州", help="jd.json 中 job_location 须包含此城市")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    jd_path = _resolve_jd_config_path(args.jd_config)
    expect_canon = canonicalize_boss_job_select(args.expect_boss_line)
    if not jd_path:
        jd_path = _find_jd_by_boss_line(expect_canon)

    if not jd_path or not Path(jd_path).exists():
        logging.error(
            "未找到 jd.json：请设置 --jd-config，或写入 ~/.jachin/memory/hr_recruitment_workflow_pointer.json "
            "的 jd_config_path，或在数据目录 %s 下存在匹配的 */jd.json",
            PLUGIN_DATA_ROOT,
        )
        return 2

    job_name = ""
    p = Path(jd_path)
    if "data" in p.parts and p.name == "jd.json":
        try:
            idx = list(p.parts).index("data")
            if idx + 1 < len(p.parts):
                job_name = p.parts[idx + 1]
        except Exception:
            pass

    jd = load_jd_config(jd_path, job_name)
    if not jd.get("job_title") and not jd.get("jd_full"):
        logging.error("JD 为空: %s", jd_path)
        return 2

    loc = (jd.get("job_location") or "").strip()
    sel = get_jd_select(jd)

    # 与 atom_post_job_boss.get_jd_select 一致：job_location 为空时默认「杭州」参与拼 Boss 行
    if args.expect_city:
        if loc:
            if args.expect_city not in loc:
                logging.error("job_location=%r 不包含期望城市 %r（请检查 jd.json）", loc, args.expect_city)
                return 2
        elif args.expect_city not in sel:
            logging.error(
                "job_location 为空且 get_jd_select=%r 中不含期望城市 %r；请在 jd.json 填写 job_location",
                sel,
                args.expect_city,
            )
            return 2
        else:
            logging.info(
                "job_location 未填，与招聘工具一致使用默认城市拼行；校验城市出现在 jd_select=%r",
                sel,
            )

    if not _jd_select_line_matches(expect_canon, sel):
        logging.error(
            "Boss 下拉行与流程 jd 不一致：期望（规范化）%r，get_jd_select(jd)=%r",
            expect_canon,
            sel,
        )
        return 2

    logging.info(
        "校验通过：jd=%s job_title=%r job_location=%r → jd_select=%r（与 expect 匹配）",
        jd_path,
        (jd.get("job_title") or "").strip(),
        loc,
        sel,
    )
    logging.info("调用 atom_greet_recommend_boss（与 L3 MCP 本地 invoke 同一实现），max_greet=%s", args.max_greet)

    result = atom_greet_recommend_boss(
        cdp_url=args.cdp,
        jd_config_path=jd_path,
        max_greet_per_run=max(1, int(args.max_greet)),
    )

    print("--- 汇总 ---")
    print("success:", result.get("success"))
    print("greeted_count:", result.get("greeted_count", 0))
    print("skipped_chat_history:", result.get("skipped_chat_history", 0))
    print("skipped_low_score:", result.get("skipped_low_score", 0))
    if result.get("error"):
        print("error:", result["error"])
    events = result.get("greet_events") or []
    print("greet_events (%d 条):" % len(events))
    print(json.dumps(events, ensure_ascii=False, indent=2))

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
