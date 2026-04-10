#!/usr/bin/env python3
"""
HR 招聘 MCP — 可选 FastMCP 入口（与 plugin.json 动态加载并存，供 Cursor / mcp_servers.json 使用）

暴露：发帖、打招呼、收网、求简历、归档、brain_filter、进度、Lark 消息与多维表同步等。
"""
import asyncio
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
from tools.atom_lark_chat import atom_lark_chat as _atom_lark_chat
from tools.atom_lark_send_message import atom_lark_send_message as _atom_lark_send_message, atom_lark_list_tasks as _atom_lark_list_tasks
from tools.atom_lark_bitable_sync import atom_lark_bitable_sync as _atom_lark_bitable_sync

# 不同版本 FastMCP 对 description / instructions 参数支持不一，避免启动即 TypeError
try:
    mcp = FastMCP(
        "hr-atomic-tools",
        description="HR 招聘原子工具箱：发布、打招呼、收网、求简历、归档、粗筛、进度查询、Lark 多维表同步、Lark 机器人发言与 AI 对话",
    )
except TypeError:
    mcp = FastMCP("hr-atomic-tools")


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
async def atom_inbox_harvester(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "资深Golang语言开发_杭州 25-40K",
    download_to_pending: bool = True,
    max_items: int = 50,
) -> dict:
    """遍历左侧求职者对话，对有「点击预览附件简历」的自动下载 PDF。需 Chrome 以 --remote-debugging-port 启动。"""
    return await asyncio.to_thread(
        _harvest_full,
        cdp_url=cdp_url,
        job_text=job_text,
        download_to_pending=download_to_pending,
        max_items=max_items,
    )


@mcp.tool()
async def atom_request_resume(
    cdp_url: str = "http://127.0.0.1:9222",
    jd_config_path: str = "",
    job_keyword: str = "",
    candidate_name: str = "付华斌",
    candidate_skill: str = "Java",
) -> dict:
    """点击求简历按钮：选择职位 → 进入候选人对话 → 若未发简历则点击求简历。传入 jd_config_path（data/{岗位名}/jd.json）时从中读取岗位配置。需 Chrome 以调试模式启动。"""
    return await asyncio.to_thread(
        _atom_request_resume,
        cdp_url=cdp_url,
        jd_config_path=jd_config_path,
        job_keyword=job_keyword,
        candidate_name=candidate_name,
        candidate_skill=candidate_skill,
    )


@mcp.tool()
async def atom_request_resume_batch(
    cdp_url: str = "http://127.0.0.1:9222",
    jd_config_path: str = "",
    job_text: str = "",
    max_items: int = 50,
) -> dict:
    """遍历沟通页左侧对话列表，对每个未发简历的对话点击求简历。传入 jd_config_path（data/{岗位名}/jd.json）时从中读取岗位配置。需 Chrome 以调试模式启动，并停留在 Boss 沟通页。"""
    return await asyncio.to_thread(
        _atom_request_resume_batch,
        cdp_url=cdp_url,
        jd_config_path=jd_config_path,
        job_text=job_text,
        max_items=max_items,
    )


@mcp.tool()
async def harvest_resume_full_flow(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "",
    jd_config_path: str = "",
    download_to_pending: bool = True,
    max_items: int = 50,
    request_if_no_resume: bool = True,
    use_all_positions: bool = False,
) -> dict:
    """收网抓取：选择职位→遍历左侧求职者→无简历则求简历，有简历则下载 PDF 到 data/{职位}/pending。可传 jd_config_path。use_all_positions=True 时选「全部职位」、忽略 job_text（仅短时联调）。需 Chrome 以调试模式启动，停留在 Boss 沟通页。"""
    return await asyncio.to_thread(
        _harvest_full,
        cdp_url=cdp_url,
        job_text=job_text or "资深Golang语言开发_杭州 25-40K",
        jd_config_path=jd_config_path,
        download_to_pending=download_to_pending,
        max_items=max_items,
        request_if_no_resume=request_if_no_resume,
        use_all_positions=use_all_positions,
    )


@mcp.tool()
async def atom_post_job_boss(
    cdp_url: str = "http://127.0.0.1:9222",
    jd_config_path: str = "",
    jd_config: str | dict | None = None,
) -> dict:
    """在 Boss 直聘自动填写并发布职位。优先传 jd_config（HR 确认的 JSON），系统会创建 data/{岗位名}/、填 jd.json 再发布；或传 jd_config_path 指向 data/{岗位名}/jd.json。需 Chrome 以调试模式启动。"""
    import json
    path_to_use = jd_config_path or ""
    if jd_config and (isinstance(jd_config, dict) or (isinstance(jd_config, str) and jd_config.strip())):
        try:
            cfg = jd_config if isinstance(jd_config, dict) else json.loads(str(jd_config))
            if isinstance(cfg, dict) and (cfg.get("job_title") or cfg.get("jd_full")):
                from tools.hr_data_paths import init_job_jd_from_template

                job_title = (cfg.get("job_title") or "").strip()
                if job_title:
                    try:
                        jd_path = init_job_jd_from_template(job_title, overrides=cfg)
                    except ValueError as e:
                        return {"success": False, "posted": False, "error": str(e)}
                    path_to_use = str(jd_path)
        except (json.JSONDecodeError, Exception):
            pass
    return await asyncio.to_thread(_atom_post_job_boss, cdp_url=cdp_url, jd_config_path=path_to_use)


@mcp.tool()
async def atom_greet_recommend_boss(cdp_url: str = "http://127.0.0.1:9222", jd_config_path: str = "") -> dict:
    """在推荐牛人页面自动筛选并打招呼：读 JD → 遍历卡片 → 跳过已沟通 → 小模型初筛(≥30%) → 打招呼，最多2人。需 Chrome 调试模式。"""
    return await asyncio.to_thread(_atom_greet_recommend_boss, cdp_url=cdp_url, jd_config_path=jd_config_path)


@mcp.tool()
def atom_get_progress() -> dict:
    """获取招聘进度（recruitment_status.json）。供 HR 被动查询「现在几个了？」使用。"""
    refresh_unprocessed_count()
    return {"success": True, "status": load_status()}


@mcp.tool()
def atom_lark_list_tasks() -> dict:
    """列出 Lark 对话中记录的待执行任务（同步、抓取、发布等）。"""
    return _atom_lark_list_tasks()


@mcp.tool()
def atom_lark_chat(user_text: str, chat_id: str = "", user_id: str = "") -> dict:
    """处理 Lark 用户消息：L3 模式转发给 Jachin；独立模式普通问题用百炼回复，任务请求只记录不执行。返回 reply 与 is_task。"""
    return _atom_lark_chat(user_text=user_text, chat_id=chat_id, user_id=user_id)


@mcp.tool()
def atom_lark_send_message(
    text: str = "",
    prompt: str = "",
    use_llm: bool = False,
    chat_id: str = "",
) -> dict:
    """让 Lark 机器人发言：发送固定文案，或根据 prompt 用阿里百炼生成回复后发送。需配置 LARK_CHAT_ID。"""
    return _atom_lark_send_message(
        text=text,
        prompt=prompt,
        use_llm=use_llm,
        chat_id=chat_id or "",
    )


@mcp.tool()
async def atom_lark_bitable_sync(
    md_path: str = "",
    app_token: str = "",
    table_id: str = "",
    log_table_id: str = "",
    field_mapping: str = "",
    dry_run: bool = False,
    notify_group: bool = True,
    chat_id: str = "",
) -> dict:
    """将排行榜 MD 导入 Lark 多维表格。每职位最多 10 条，覆盖式更新不累加；可选 log_table_id 记录更新日志；完成后通知 HR。"""
    if not md_path:
        return {"success": False, "error": "md_path 不能为空"}
    return await asyncio.to_thread(
        _atom_lark_bitable_sync,
        md_path=md_path,
        app_token=app_token or "",
        table_id=table_id or "",
        log_table_id=log_table_id or "",
        field_mapping=field_mapping or "",
        dry_run=dry_run,
        notify_group=notify_group,
        chat_id=chat_id or "",
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
