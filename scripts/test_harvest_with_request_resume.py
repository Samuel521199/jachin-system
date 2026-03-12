#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试收网抓取：无简历则求简历，有简历则下载 PDF。

流程：
  1. 选择职位（job_text 或 jd_config_path）
  2. 遍历左侧候选人会话
  3. 无简历 → 点击「求简历」
  4. 有简历 → 下载 PDF 到 data/{职位}/pending

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 登录 Boss 直聘，打开「沟通」页
  3. 从项目根目录运行: python scripts\\test_harvest_with_request_resume.py

用法示例：
  python scripts\\test_harvest_with_request_resume.py --job "高级golang开发工程师_杭州 19-20K"
  python scripts\\test_harvest_with_request_resume.py --jd-config  # 从 data/高级golang开发工程师/jd.json 读取
  python scripts\\test_harvest_with_request_resume.py --no-request  # 不执行求简历，仅下载
"""
import argparse
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_TOOLS = ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
sys.path.insert(0, str(PLUGIN_TOOLS))

from tools.boss_harvest_orchestrator import harvest_resume_full_flow
from tools.hr_data_paths import get_job_jd_path, init_job_jd_from_template

JOB_TEXT = "高级golang开发工程师_杭州 19-20K"
JOB_TITLE = "高级golang开发工程师"


def ensure_jd_config() -> str:
    """确保 data/高级golang开发工程师/jd.json 存在"""
    jd_path = get_job_jd_path(JOB_TITLE)
    if jd_path.exists():
        return str(jd_path)
    init_job_jd_from_template(JOB_TITLE, {
        "job_title": JOB_TITLE,
        "salary_min": 19,
        "salary_max": 20,
        "job_keywords": ["Golang", "Go", "微服务"],
    })
    print(f"[初始化] 已创建 {jd_path}")
    return str(jd_path)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="测试收网抓取：无简历→求简历，有简历→下载")
    p.add_argument("--job", default=JOB_TEXT, help="职位文本（Boss 全部职位下拉匹配）")
    p.add_argument("--max", type=int, default=20, help="最多遍历对话数")
    p.add_argument("--jd-config", action="store_true", help="从 data/{岗位名}/jd.json 读取")
    p.add_argument("--no-request", action="store_true", help="不执行求简历，仅下载已有简历")
    args = p.parse_args()

    jd_config_path = ""
    job_text = args.job
    if args.jd_config:
        jd_config_path = ensure_jd_config()
        job_text = ""
        print(f"[配置] 使用 jd_config_path: {jd_config_path}\n")

    result = harvest_resume_full_flow(
        cdp_url="http://127.0.0.1:9222",
        job_text=job_text,
        jd_config_path=jd_config_path,
        download_to_pending=True,
        max_items=args.max,
        request_if_no_resume=not args.no_request,
    )

    print("=" * 55)
    print("收网抓取 结果")
    print("=" * 55)
    print("成功:", result.get("success"))
    print("下载简历数:", result.get("downloaded", 0))
    print("求简历数:", result.get("requested_count", 0))
    print("PDF 路径:", result.get("pdf_paths", [])[:5], "..." if len(result.get("pdf_paths", [])) > 5 else "")
    if result.get("processed"):
        print("\n处理明细:")
        for item in result["processed"]:
            action = item.get("action", "")
            extra = ""
            if action == "downloaded" and item.get("path"):
                extra = f" -> {item['path']}"
            elif action == "request_sent":
                extra = " (已点击求简历)"
            elif action in ("download_failed", "error") and item.get("error"):
                extra = f" ({item['error']})"
            print(f"  - {item.get('label')}: {action}{extra}")
    if result.get("error"):
        print("\n错误:", result["error"])
