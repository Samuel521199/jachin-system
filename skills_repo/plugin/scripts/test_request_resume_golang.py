#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试「求简历」MCP：职位「高级golang开发工程师_杭州 19-20K」

支持两种模式：
  batch  - 批量遍历沟通页左侧对话，对未发简历的点击求简历
  single - 单人模式，指定候选人姓名和技能标签

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 登录 Boss 直聘，打开「沟通」页
  3. 确保职位下拉中有「高级golang开发工程师_杭州 19-20K」选项
  4. 运行本脚本

用法示例：
  python scripts\\test_request_resume_golang.py                    # 批量，默认最多 50 人
  python scripts\\test_request_resume_golang.py --max 20            # 批量，最多 20 人
  python scripts\\test_request_resume_golang.py --mode single --name 张三 --skill Golang  # 单人
  python scripts\\test_request_resume_golang.py --jd-config         # 从 data/高级golang开发工程师/jd.json 读取
"""
import argparse
import sys
from pathlib import Path

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "com.jachin.hr.recruitment"))

from tools.atom_request_resume import atom_request_resume, atom_request_resume_batch
from tools.hr_data_paths import get_job_jd_path, init_job_jd_from_template

# 目标职位
JOB_TEXT = "高级golang开发工程师_杭州 19-20K"
JOB_TITLE = "高级golang开发工程师"
JOB_KEYWORD = "Golang"


def ensure_jd_config() -> str:
    """确保 data/高级golang开发工程师/jd.json 存在，返回路径"""
    jd_path = get_job_jd_path(JOB_TITLE)
    if jd_path.exists():
        return str(jd_path)
    overrides = {
        "job_title": JOB_TITLE,
        "salary_min": 19,
        "salary_max": 20,
        "job_keywords": ["Golang", "Go", "微服务"],
        "experience": "3-5年",
        "education": "本科",
    }
    init_job_jd_from_template(JOB_TITLE, overrides)
    print(f"[初始化] 已创建 {jd_path}")
    return str(jd_path)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="测试求简历 MCP - 职位: 高级golang开发工程师_杭州 19-20K")
    p.add_argument("--mode", choices=["batch", "single"], default="batch", help="batch=批量, single=单人")
    p.add_argument("--max", type=int, default=50, help="批量模式：最多处理几个对话")
    p.add_argument("--name", default="", help="单人模式：候选人姓名")
    p.add_argument("--skill", default="Golang", help="单人模式：候选人技能标签（如 Golang）")
    p.add_argument(
        "--jd-config",
        action="store_true",
        help="从 data/高级golang开发工程师/jd.json 读取岗位配置（不存在则自动创建）",
    )
    args = p.parse_args()

    cdp_url = "http://127.0.0.1:9222"
    jd_config_path = ""
    if args.jd_config:
        jd_config_path = ensure_jd_config()
        print(f"[配置] 使用 jd_config_path: {jd_config_path}\n")

    if args.mode == "batch":
        result = atom_request_resume_batch(
            cdp_url=cdp_url,
            jd_config_path=jd_config_path or "",
            job_text=JOB_TEXT if not jd_config_path else "",
            max_items=args.max,
        )
        print("=" * 50)
        print("批量求简历 结果")
        print("=" * 50)
        print("成功:", result.get("success"))
        print("求简历数:", result.get("requested_count", 0))
        print("已发简历跳过:", result.get("skipped_has_resume", 0))
        if result.get("processed"):
            print("\n处理明细:")
            for item in result["processed"]:
                print(f"  - {item.get('label')}: {item.get('action')}")
    else:
        name = args.name or "付华斌"  # 单人模式需指定候选人，默认测试用
        result = atom_request_resume(
            cdp_url=cdp_url,
            jd_config_path=jd_config_path or "",
            job_keyword=JOB_KEYWORD if not jd_config_path else "",
            candidate_name=name,
            candidate_skill=args.skill,
        )
        print("=" * 50)
        print("单人求简历 结果")
        print("=" * 50)
        print("成功:", result.get("success"))
        print("已执行求简历:", result.get("request_sent"))

    if result.get("error"):
        print("\n错误:", result["error"])
