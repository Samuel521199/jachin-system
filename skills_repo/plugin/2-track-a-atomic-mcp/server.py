#!/usr/bin/env python3
"""
轨道 A - 原子 MCP Server
暴露原子工具：atom_post_job_boss, atom_greet_recommend_boss, harvest_resume_full_flow, atom_request_resume_batch, local_archiver, brain_filter
"""
import sys
from pathlib import Path

# 确保 tools 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    def main():
        print('{"error": "请安装 mcp: pip install mcp"}', flush=True)
        sys.exit(1)
    if __name__ == "__main__":
        main()
    raise SystemExit

from tools.local_archiver import local_archiver as _local_archiver
from tools.boss_harvest_orchestrator import harvest_resume_full_flow as _harvest_full
from tools.atom_request_resume import atom_request_resume as _atom_request_resume, atom_request_resume_batch as _atom_request_resume_batch
from tools.brain_filter import brain_filter as _brain_filter
from tools.atom_post_job_boss import atom_post_job_boss as _atom_post_job_boss
from tools.atom_greet_recommend_boss import atom_greet_recommend_boss as _atom_greet_recommend_boss
from tools.recruitment_status import load_status, refresh_unprocessed_count

mcp = FastMCP("hr-atomic-tools", description="HR 招聘原子工具箱：发布、打招呼、收网、求简历、归档、粗筛、进度查询")


@mcp.tool()
def brain_filter(resume_text: str = "", hr_criteria: str = "") -> dict:
    """小脑粗筛：底线过滤（学历、年限）。返回 pass/reason/score。"""
    return _brain_filter(resume_text=resume_text, hr_criteria=hr_criteria or "学历本科，经验3年")


@mcp.tool()
def local_archiver(pdf_path: str = "", pdf_bytes: str = "", candidate_name: str = "", file_label: str = "", job_folder: str = "") -> dict:
    """将 PDF 保存到 data/pending/<职位>/。job_folder 指定职位文件夹；不传则从 file_label 的【】内提取。"""
    kwargs = {"pdf_path": pdf_path, "candidate_name": candidate_name, "file_label": file_label, "job_folder": job_folder}
    if pdf_bytes:
        import base64
        try:
            b = base64.b64decode(pdf_bytes)
        except Exception:
            b = pdf_bytes.encode() if isinstance(pdf_bytes, str) else pdf_bytes
        if b:
            kwargs["pdf_bytes"] = b
    return _local_archiver(**kwargs)


@mcp.tool()
def atom_inbox_harvester(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "资深Golang语言开发_杭州 25-40K",
    download_to_pending: bool = True,
    max_items: int = 50,
) -> dict:
    """遍历左侧求职者对话，对有「点击预览附件简历」的自动下载 PDF。需 Chrome 以 --remote-debugging-port 启动。"""
    return _harvest_full(
        cdp_url=cdp_url,
        job_text=job_text,
        download_to_pending=download_to_pending,
        max_items=max_items,
    )


@mcp.tool()
def atom_request_resume(
    cdp_url: str = "http://127.0.0.1:9222",
    job_keyword: str = "java",
    candidate_name: str = "付华斌",
    candidate_skill: str = "Java",
) -> dict:
    """点击求简历按钮：选择职位 → 进入候选人对话 → 若未发简历则点击求简历。需 Chrome 以调试模式启动。"""
    return _atom_request_resume(
        cdp_url=cdp_url,
        job_keyword=job_keyword,
        candidate_name=candidate_name,
        candidate_skill=candidate_skill,
    )


@mcp.tool()
def atom_request_resume_batch(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "资深Golang语言开发_杭州 25-40K",
    max_items: int = 50,
) -> dict:
    """遍历沟通页左侧对话列表，对每个未发简历的对话点击求简历。需 Chrome 以调试模式启动，并停留在 Boss 沟通页。"""
    return _atom_request_resume_batch(
        cdp_url=cdp_url,
        job_text=job_text,
        max_items=max_items,
    )


@mcp.tool()
def harvest_resume_full_flow(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "资深Golang语言开发_杭州 25-40K",
    download_to_pending: bool = True,
    max_items: int = 50,
) -> dict:
    """选择职位→遍历左侧求职者→若有「点击预览附件简历」则下载 PDF。需 Chrome 以调试模式启动，停留在 Boss 沟通页。"""
    return _harvest_full(
        cdp_url=cdp_url,
        job_text=job_text,
        download_to_pending=download_to_pending,
        max_items=max_items,
    )


@mcp.tool()
def atom_post_job_boss(cdp_url: str = "http://127.0.0.1:9222", jd_config_path: str = "") -> dict:
    """在 Boss 直聘自动填写并发布职位。读取 data/jd_to_publish.json。需 Chrome 以调试模式启动。"""
    return _atom_post_job_boss(cdp_url=cdp_url, jd_config_path=jd_config_path)


@mcp.tool()
def atom_greet_recommend_boss(cdp_url: str = "http://127.0.0.1:9222", jd_config_path: str = "") -> dict:
    """在推荐牛人页面自动筛选并打招呼：读 JD → 遍历卡片 → 跳过已沟通 → 小模型初筛(≥30%) → 打招呼，最多2人。需 Chrome 调试模式。"""
    return _atom_greet_recommend_boss(cdp_url=cdp_url, jd_config_path=jd_config_path)


@mcp.tool()
def atom_get_progress() -> dict:
    """获取招聘进度（recruitment_status.json）。供 HR 被动查询「现在几个了？」使用。"""
    refresh_unprocessed_count()
    return {"success": True, "status": load_status()}


if __name__ == "__main__":
    mcp.run(transport="stdio")
