#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试收网 + HR 透析镜 全链路：抓取简历 → 分析

流程：
  1. 创建岗位 JD 配置 data/{岗位名}/jd.json
  2. 调用 L3 招聘任务 API（收网 → HR 透析镜分析）
  3. 当 pending 中简历数 >= analyze_threshold 时进行分析，输出到 result/ 和 排行榜_Summary.md

前置条件：
  1. L3 已启动（tauri dev 或 python -m l3_node --gateway）
  2. Chrome 以调试模式启动（scripts\\launch_chrome_debug.ps1），登录 Boss 直聘并打开「沟通」页
  3. 从项目根目录运行: python scripts\\test_recruitment_harvest_analyze.py

用法示例：
  python scripts\\test_recruitment_harvest_analyze.py
  python scripts\\test_recruitment_harvest_analyze.py --analyze-threshold 3 --max-count 25
  python scripts\\test_recruitment_harvest_analyze.py --max-count 30  # 最多遍历 30 个对话
"""
import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 默认岗位 JD：Go 后端工程师
DEFAULT_JD = """**岗位职责：**

1. 负责核心业务系统的架构设计与开发，保障系统高可用性与可扩展性；
2. 参与技术选型与代码评审，持续优化系统性能与稳定性；
3. 配合产品团队完成需求分析、技术方案设计与落地实施；
4. 编写高质量技术文档，指导初级工程师成长；
5. 负责线上问题排查与故障处理，保障SLA达标。

**任职要求：**

1. 计算机相关专业本科及以上学历，3-5年Go语言开发经验；
2. 精通Go语言基础，熟悉GMP模型、并发编程及内存管理；
3. 熟练掌握MySQL、Redis、Kafka等常用中间件的使用与调优；
4. 熟悉Docker、Kubernetes等容器化技术，有微服务架构实战经验；
5. 具备良好的团队协作能力与问题解决能力，对技术有热情；
6. 有电商、金融或高并发场景项目经验者优先考虑。"""

JOB_NAME = "Go后端工程师"
L3_HTTP_BASE = "http://127.0.0.1:18991"
PLUGIN_DATA = ROOT / "skills_repo" / "plugin" / "data"


def ensure_l3_running() -> bool:
    """检测 L3 HTTP 是否可达"""
    try:
        import urllib.request
        req = urllib.request.urlopen(f"{L3_HTTP_BASE}/api/v3/skills", timeout=3)
        return req.getcode() == 200
    except Exception:
        return False


def ensure_jd_config(job_name: str, jd_full: str, job_title: str = None, salary_min: int = 19, salary_max: int = 25) -> Path:
    """确保 data/{岗位名}/jd.json 存在"""
    sys.path.insert(0, str(ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"))
    from tools.hr_data_paths import init_job_jd_from_template, get_job_jd_path

    title = job_title or job_name
    jd_path = get_job_jd_path(job_name)
    if jd_path.exists():
        data = json.loads(jd_path.read_text(encoding="utf-8"))
        data["jd_full"] = jd_full
        data["job_title"] = title
        data["salary_min"] = salary_min
        data["salary_max"] = salary_max
        jd_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return jd_path
    init_job_jd_from_template(job_name, {
        "job_title": title,
        "jd_full": jd_full,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "job_keywords": ["Go", "Golang", "微服务"],
        "experience": "3-5年",
        "education": "本科",
    })
    return jd_path


def run_recruitment_via_api(
    job_name: str,
    jd_content: str,
    max_count: int = 20,
    filter_tab: str = "全部",
    request_resume: bool = True,
    analyze_threshold: int = 2,
) -> dict:
    """
    调用 L3 POST /api/recruitment/start_task。
    注意：API 会收网后分析所有下载的 PDF，analyze_threshold 仅用于本脚本的提示与后续扩展。
    """
    import urllib.request

    url = f"{L3_HTTP_BASE}/api/recruitment/start_task"
    body = json.dumps({
        "job_name": job_name,
        "jd_content": jd_content,
        "max_count": max_count,
        "filter_tab": filter_tab,
        "request_resume": request_resume,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=600)
    lines = resp.read().decode("utf-8").strip().split("\n")
    events = []
    for line in lines:
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return {"ok": True, "events": events}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="测试收网 + HR 透析镜：抓取简历后分析")
    p.add_argument("--job-name", default=JOB_NAME, help="岗位名称")
    p.add_argument("--jd", default=DEFAULT_JD, help="岗位 JD 内容（或留空用内置）")
    p.add_argument("--analyze-threshold", type=int, default=2, help="满 N 份简历后进行分析（当前 API 会分析全部下载的）")
    p.add_argument("--max-count", type=int, default=20, help="收网最多遍历对话数")
    args = p.parse_args()

    job_name = args.job_name.strip()
    jd_content = (args.jd or DEFAULT_JD).strip()

    print("=" * 60)
    print("收网 + HR 透析镜 测试")
    print("=" * 60)
    print(f"岗位: {job_name}")
    print(f"分析阈值: {args.analyze_threshold}")
    print()

    ensure_jd_config(job_name, jd_content, job_title=job_name, salary_min=19, salary_max=25)
    print(f"[1/2] JD 配置已就绪: data/{job_name}/jd.json")

    if not ensure_l3_running():
        print("\n❌ L3 未启动！请先运行：")
        print("   - tauri dev（桌面端）")
        print("   - 或 python -m l3_node --gateway")
        print("   - 或 .\\scripts\\start-layer3.ps1")
        sys.exit(1)
    print("[2/2] L3 已就绪")
    print("\n⏳ 正在调用 L3 招聘任务（收网 → 分析）...")
    try:
        result = run_recruitment_via_api(
            job_name=job_name,
            jd_content=jd_content,
            max_count=args.max_count,
            analyze_threshold=args.analyze_threshold,
        )
        print("\n" + "=" * 60)
        print("任务完成")
        print("=" * 60)
        if result.get("ok"):
            events = result.get("events", [])
            for ev in events:
                msg = ev.get("msg", "")
                if msg:
                    print(f"  {msg}")
            print("\n报告输出: skills_repo/plugin/data/{}/result/".format(job_name))
            print("排行榜: skills_repo/plugin/data/{}/排行榜_Summary.md".format(job_name))
        else:
            print("错误:", result.get("error", "未知"))
    except Exception as e:
        print(f"\n❌ 调用失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
