#!/usr/bin/env python3
"""
L3 本地伴生 MCP - Boss 直聘收网

高强度本地 RPA（依赖本机 IP、浏览器 Cookie），仅运行于 L3 客户端本机，
通过 Stdio 与 L3 主进程通信，绝不启动 HTTP 服务。

唤醒方式：
  python -m l3_client.local_mcps.boss_harvester.server
  或
  python l3_client/local_mcps/boss_harvester/server.py

数据卷：~/.jachin/client_volumes/{target_volume}
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# 项目根与 plugin 路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PLUGIN_TOOLS = _PROJECT_ROOT / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"
if str(_PLUGIN_TOOLS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_TOOLS))

# L3 本地数据卷管线（与 L2 云端 ~/.jachin/volumes 隔离）
L3_VOLUME_ROOT = Path(os.path.expanduser("~/.jachin/client_volumes"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("boss_harvester_mcp")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    logger.error("请安装 mcp: pip install mcp")
    sys.exit(1)

mcp = FastMCP(
    "boss-harvester",
    description="Boss 直聘收网（L3 本地 RPA）：选择职位→遍历消息→下载 PDF / 求简历。需 Chrome 以 --remote-debugging-port=9222 启动。",
)


@mcp.tool()
def atom_inbox_harvester(
    job_name: str,
    max_count: int = 20,
    target_volume: str = "global_resume_pool",
    filter_tab: str = "全部",
    request_if_no_resume: bool = True,
    cdp_url: str = "http://127.0.0.1:9222",
    use_all_positions: bool = False,
) -> dict:
    """
    Boss 直聘收网：选择职位 → 遍历消息列表 → 有附件简历则下载 PDF；无简历则点击「求简历」。
    PDF 保存到 L3 本地数据卷 ~/.jachin/client_volumes/{target_volume}，返回绝对路径供 Wasm 沙箱读取。

    Args:
        job_name: 岗位名称，需与 Boss「全部职位」下拉显示完全一致（如 Java_杭州 4-6K）
        max_count: 最大处理数量
        target_volume: 数据卷名称
        filter_tab: 消息列表 Tab（全部/新招呼，默认全部）
        request_if_no_resume: 无附件简历时是否点击求简历
        cdp_url: Chrome 调试端口地址
        use_all_positions: True 时选「全部职位」、忽略 job_name（仅短时联调）；默认 False 按 job_name 选职位
    """
    if not job_name or not str(job_name).strip():
        return {"status": "error", "error": "job_name 为必填参数"}

    job_name = str(job_name).strip()
    target_volume = (target_volume or "global_resume_pool").strip()
    filter_tab = (filter_tab or "全部").strip()
    save_dir = L3_VOLUME_ROOT / target_volume
    save_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[boss_harvester] job_name=%s max_count=%d volume=%s save_dir=%s",
                job_name, max_count, target_volume, save_dir)

    try:
        from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow
    except ImportError as e:
        return {"status": "error", "error": f"导入失败: {e}"}

    raw = atom_inbox_harvester_full_flow(
        cdp_url=cdp_url,
        job_text=job_name,
        download_to_pending=True,
        max_items=max_count,
        save_dir=str(save_dir),
        filter_tab=filter_tab,
        request_if_no_resume=request_if_no_resume,
        use_all_positions=use_all_positions,
    )

    downloaded = raw.get("downloaded", 0)
    requested = raw.get("requested_count", 0)
    success = raw.get("success", False)
    err = raw.get("error", "")
    pdf_paths = raw.get("pdf_paths", [])

    # 强制返回绝对路径，供 L3 Wasm 沙箱读取
    abs_paths = [str(Path(p).resolve()) for p in pdf_paths if p]

    if not success and err:
        return {
            "status": "error",
            "job_name": job_name,
            "volume": target_volume,
            "downloaded_count": 0,
            "requested_count": 0,
            "pdf_paths": [],
            "error": err,
        }

    return {
        "status": "success",
        "job_name": job_name,
        "downloaded_count": downloaded,
        "requested_count": requested,
        "volume": target_volume,
        "volume_root": str(L3_VOLUME_ROOT),
        "pdf_paths": abs_paths,
    }


def main():
    """Stdio 模式启动，与 L3 主进程通过 stdin/stdout 通信。"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
