"""
编排脚本：委托 atom_inbox_harvester_full_flow 实现选择职位 → 遍历左侧求职者 → 下载 PDF。
保留 harvest_resume_full_flow 以兼容现有调用（cron_runner、MCP 等）。
"""
from .atom_inbox_harvester import atom_inbox_harvester_full_flow


def harvest_resume_full_flow(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "资深Golang语言开发_杭州 25-40K",
    download_to_pending: bool = True,
    max_items: int = 50,
    debug: bool = False,
) -> dict:
    """
    完整流程：在「全部职位」中选择对应职位 → 遍历左侧所有候选人会话 →
    若有「点击预览附件简历」则下载 PDF，否则跳过继续下一个。

    前置：Chrome 以 --remote-debugging-port 启动，停留在 Boss 沟通页。
    """
    return atom_inbox_harvester_full_flow(
        cdp_url=cdp_url,
        job_text=job_text,
        download_to_pending=download_to_pending,
        max_items=max_items,
    )
