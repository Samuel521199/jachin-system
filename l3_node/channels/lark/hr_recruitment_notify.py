"""
HR 无人值守：向飞书会话推送抓取进度与状态简报。

**原则**：先由本模块生成「系统草稿」，再经 **大模型润色**（`hr_lark_llm_polish`）后发飞书；
技术细节进日志并作为润色参考，不原样出现在飞书。关闭润色：`JACHIN_HR_LARK_LLM_POLISH=0`。

依赖 LARK_APP_ID/SECRET；会话 ID 优先环境变量 LARK_CHAT_ID，否则使用
~/.jachin/memory/hr_recruitment_workflow_pointer.json 中的 lark_chat_id（由 atom_lark_chat 写入）。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_RECENT_OTHER_JOBS = 3


def _sanitize_hr_job_folder(job_name: str, max_len: int = 60) -> str:
    """与插件 ``sanitize_job_folder`` 一致，避免 notify 依赖 sys.path 失败。"""
    illegal = r'\/:*?"<>|'
    s = job_name or ""
    for c in illegal:
        s = s.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in s)
    return (s.strip("_")[:max_len] or "未分类")


def _hr_recruitment_workspace_root() -> Path:
    return Path.home() / ".jachin" / "workspace" / "hr_recruitment"


def _jd_show_in_hr_briefing_dict(doc: Any) -> bool:
    """
    与插件 ``tools.hr_data_paths.jd_show_in_hr_briefing`` 语义一致（字段名 ``show_in_hr_briefing``）：
    缺省 True；为 false 时飞书简报 L3 不列出该岗。
    """
    if not isinstance(doc, dict):
        return True
    v = doc.get("show_in_hr_briefing")
    if v is None:
        return True
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "否", "关", "off")
    if isinstance(v, (int, float)):
        return int(v) != 0
    return bool(v)


def _brief_line_for_other_job(rs: Any, job_title: str) -> str:
    """其他岗位一行摘要（用于 L3）；rs 可为 None。"""
    jt = (job_title or "").strip() or "未命名"
    if rs is not None and hasattr(rs, "get_recruitment_status_digest"):
        try:
            dd = rs.get_recruitment_status_digest(jt)
        except Exception:
            dd = {}
        if isinstance(dd, dict) and dd.get("has_active_job"):
            n = int(dd.get("pending_pdf_count") or 0)
            cap = int(dd.get("collect_cap") or 0)
            bits: list[str] = []
            if cap > 0:
                bits.append(f"简历 {n}/{cap}")
            elif n > 0:
                bits.append(f"简历约 {n} 份")
            if dd.get("greet_only_scheduler_active"):
                gd = int(dd.get("greet_only_done") or 0)
                gt = int(dd.get("greet_only_total_target") or 0)
                bits.append(f"仅打招呼进行中 {gd}/{gt}")
            elif hasattr(rs, "incomplete_greet_only_snapshot"):
                jf = (dd.get("job_folder") or "").strip()
                if jf:
                    try:
                        snap = rs.incomplete_greet_only_snapshot(jf)
                    except Exception:
                        snap = None
                    if isinstance(snap, dict):
                        bits.append(f"仅打招呼未完 {snap.get('done')}/{snap.get('target')}")
            if dd.get("scheduler_active"):
                bits.append("调度运行中")
            elif dd.get("globally_stopped"):
                bits.append("全局已暂停")
            else:
                bits.append("调度未开")
            return f"· **{jt}**：{' · '.join(bits) if bits else '无摘要'}"
    # 无调度器：仅磁盘
    jf = _sanitize_hr_job_folder(jt)
    root = _hr_recruitment_workspace_root() / jf
    if not root.is_dir():
        for p in _hr_recruitment_workspace_root().iterdir():
            if not p.is_dir():
                continue
            try:
                doc = json.loads((p / "jd.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(doc, dict) and (doc.get("job_title") or "").strip() == jt:
                if not _jd_show_in_hr_briefing_dict(doc):
                    continue
                root = p
                break
    pdf_n = 0
    pend = root / "pending"
    if pend.is_dir():
        try:
            pdf_n = len([x for x in pend.rglob("*.pdf") if x.is_file()])
        except OSError:
            pass
    return f"· **{jt}**：pending 约 **{pdf_n}** 份 PDF（调度状态需加载招聘模块）"


def _collect_recent_other_jobs(
    *,
    exclude_jf: str,
    exclude_title: str,
    rs: Any,
    limit: int = _MAX_RECENT_OTHER_JOBS,
) -> list[str]:
    """L3：最近有 jd 的其他岗位，每人一行中文。"""
    exclude_jf = (exclude_jf or "").strip()
    exclude_title = (exclude_title or "").strip()
    seen: set[str] = set()
    if exclude_jf:
        seen.add(exclude_jf)
    if exclude_title:
        seen.add(_sanitize_hr_job_folder(exclude_title))

    ordered_titles: list[str] = []

    try:
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        ptr = get_hr_recruitment_workflow_pointer()
        jobs = ptr.get("jobs") if isinstance(ptr.get("jobs"), list) else []
        for x in jobs:
            if not isinstance(x, dict):
                continue
            jn = (x.get("job_name") or "").strip()
            if not jn:
                continue
            jf_e = (x.get("job_folder") or "").strip()
            if jf_e:
                _jdp = _hr_recruitment_workspace_root() / jf_e / "jd.json"
                if _jdp.is_file():
                    try:
                        _doc = json.loads(_jdp.read_text(encoding="utf-8"))
                        if isinstance(_doc, dict) and not _jd_show_in_hr_briefing_dict(_doc):
                            continue
                    except Exception:
                        pass
            key = _sanitize_hr_job_folder(jn)
            if key in seen:
                continue
            seen.add(key)
            ordered_titles.append(jn)
            if len(ordered_titles) >= limit:
                return [_brief_line_for_other_job(rs, t) for t in ordered_titles]
    except Exception as e:
        logger.debug("[Lark HR] 读指针 jobs 最近岗跳过: %s", e)

    root = _hr_recruitment_workspace_root()
    if not root.is_dir():
        return [_brief_line_for_other_job(rs, t) for t in ordered_titles[:limit]]

    candidates: list[tuple[float, str]] = []
    try:
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            jd = sub / "jd.json"
            if not jd.is_file():
                continue
            try:
                doc = json.loads(jd.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            if not _jd_show_in_hr_briefing_dict(doc):
                continue
            title = (doc.get("job_title") or sub.name).strip() or sub.name
            key = _sanitize_hr_job_folder(title)
            if key in seen:
                continue
            try:
                mt = jd.stat().st_mtime
            except OSError:
                mt = 0.0
            candidates.append((mt, title))
    except Exception as e:
        logger.debug("[Lark HR] 扫描 hr_recruitment 目录跳过: %s", e)

    candidates.sort(key=lambda x: -x[0])
    for _mt, title in candidates:
        key = _sanitize_hr_job_folder(title)
        if key in seen:
            continue
        seen.add(key)
        if title in ordered_titles:
            continue
        ordered_titles.append(title)
        if len(ordered_titles) >= limit:
            break

    return [_brief_line_for_other_job(rs, t) for t in ordered_titles[:limit]]


def _lark_im_yaml_credentials() -> tuple[str, str, str | None]:
    """
    从 ~/.jachin/config/im_channels.yaml 读取 Lark app_id / app_secret / domain（与长连接入口一致）。
    返回 (app_id, app_secret, api_base)；api_base 为 None 时用 get_lark_api_base()。
    """
    try:
        from l3_node.im_channels.config import load_config
        from l3_node.channels.lark.client import _api_base_from_domain

        ch = (load_config().get("im_channels") or {}).get("lark") or {}
        if not isinstance(ch, dict) or not ch.get("enabled"):
            return "", "", None
        aid = (ch.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
        sec = (ch.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
        dom = (ch.get("domain") or "").strip()
        api_base = _api_base_from_domain(dom) if dom else None
        return aid, sec, api_base
    except Exception as e:
        logger.debug("[Lark HR] 读取 im_channels.yaml 失败: %s", e)
        return "", "", None


def _lark_first_chat_id_from_yaml() -> str:
    try:
        from l3_node.im_channels.config import load_config

        ch = (load_config().get("im_channels") or {}).get("lark") or {}
        cids = ch.get("chat_ids") or []
        if isinstance(cids, list) and cids:
            return str(cids[0]).strip()
    except Exception:
        pass
    return ""


def send_hr_recruitment_progress_message(
    text: str,
    *,
    technical_detail: str | None = None,
    message_kind: str = "hr_progress",
) -> dict[str, Any]:
    """
    向当前 HR 招聘关联的飞书会话发送文本（进度类）。
    technical_detail：写入日志，并作为 LLM 润色的内部背景（不会原样出现在飞书）。
    message_kind：润色提示用场景标签（如 hr_briefing、hr_scheduler_confirm）。
    全局关闭润色：JACHIN_HR_LARK_LLM_POLISH=0。
    **上线/日报简报**（hr_briefing）默认 **不走大模型**（避免重启后阻塞数十秒）；需要润色时设
    JACHIN_HR_LARK_BRIEFING_LLM_POLISH=1|true|on。
    **收网进度**（hr_harvest_tick）默认 **不走润色**，避免模型改写份数；需要润色时设
    JACHIN_HR_LARK_HARVEST_PROGRESS_POLISH=1|true|on。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "skipped": True, "reason": "empty"}
    if technical_detail and str(technical_detail).strip():
        logger.info(
            "[Lark HR] 即将发送飞书；技术明细（仅日志/润色参考）：\n%s",
            str(technical_detail).strip()[:16000],
        )

    kind = (message_kind or "").strip() or "hr_progress"
    briefing_polish = (os.environ.get("JACHIN_HR_LARK_BRIEFING_LLM_POLISH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    skip_polish_for_briefing = kind == "hr_briefing" and not briefing_polish
    harvest_polish = (os.environ.get("JACHIN_HR_LARK_HARVEST_PROGRESS_POLISH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    skip_polish_for_harvest_family = kind in ("hr_harvest_tick", "hr_resume_target_clarify") and not harvest_polish

    text_to_send = text
    if skip_polish_for_briefing:
        logger.debug("[Lark HR] hr_briefing 跳过 LLM 润色（直接发草稿）；需润色请设 JACHIN_HR_LARK_BRIEFING_LLM_POLISH=1")
    elif skip_polish_for_harvest_family:
        logger.debug(
            "[Lark HR] %s 跳过 LLM 润色（直接发草稿，保证份数与系统一致）；"
            "需润色请设 JACHIN_HR_LARK_HARVEST_PROGRESS_POLISH=1",
            kind,
        )
    else:
        try:
            from l3_node.channels.lark.hr_lark_llm_polish import polish_hr_lark_message_sync

            text_to_send = polish_hr_lark_message_sync(
                text,
                technical_detail=technical_detail,
                message_kind=message_kind,
            )
            if text_to_send != text:
                logger.debug("[Lark HR] 润色前草稿全文:\n%s", text[:6000])
        except Exception as e:
            logger.debug("[Lark HR] 润色跳过: %s", e)
            text_to_send = text

    try:
        from l3_node.channels.lark.client import get_lark_api_base, is_lark_api_configured
        from l3_node.channels.lark.im import send_text
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer
    except ImportError as e:
        logger.debug("[Lark HR] 依赖缺失: %s", e)
        return {"ok": False, "skipped": True, "reason": "import"}

    cid = (os.environ.get("LARK_CHAT_ID") or "").strip()
    if not cid:
        cid = (get_hr_recruitment_workflow_pointer().get("lark_chat_id") or "").strip()
    if not cid:
        cid = _lark_first_chat_id_from_yaml()
    if not cid:
        logger.info("[Lark HR] 进度/简报未发送: 无 chat_id（可设 LARK_CHAT_ID、或 atom_lark_chat 写入指针、或在 im_channels.yaml 的 lark.chat_ids 填群 ID）")
        return {"ok": False, "skipped": True, "reason": "no_chat_id"}

    y_aid, y_sec, y_base = _lark_im_yaml_credentials()
    use_yaml_creds = not is_lark_api_configured() and bool(y_aid and y_sec)
    if not is_lark_api_configured() and not use_yaml_creds:
        logger.info(
            "[Lark HR] 进度/简报未发送: 无 Lark 凭证（请设 LARK_APP_ID/SECRET 或在 im_channels.yaml 的 lark 下填写 app_id/app_secret）"
        )
        return {"ok": False, "skipped": True, "reason": "no_lark_credentials"}

    try:
        if use_yaml_creds:
            base = y_base or get_lark_api_base()
            return send_text(cid, text_to_send, app_id=y_aid, app_secret=y_sec, api_base=base)
        return send_text(cid, text_to_send)
    except Exception as e:
        logger.warning("[Lark HR] 发送进度失败: %s", e)
        return {"status": "error", "error": str(e)}


def build_hr_incremental_resume_target_clarify_feishu_text(
    *,
    pending_count: int,
    stated_cumulative_target: int,
    job_title: str = "",
) -> str:
    """pending 已多于用户刚写入的「累计上限」时，说明「累计 vs 再收」并给出口令。"""
    jt = (job_title or "").strip() or "当前岗位"
    total_if_plus = int(pending_count) + int(stated_cumulative_target)
    return (
        f"【收网目标确认 · {jt}】\n"
        f"pending 里**已有 {int(pending_count)} 份**简历。\n"
        f"本句里的 **{int(stated_cumulative_target)}** 已按 **「累计收满即停」** 写入 jd（即一共收到 **{int(stated_cumulative_target)}** 份为止）。\n\n"
        f"若您本意是 **在现有 {int(pending_count)} 份上再收 {int(stated_cumulative_target)} 份**（累计约 **{total_if_plus}** 份），请发：\n"
        f"· **「再抓 {int(stated_cumulative_target)} 份」**，或\n"
        f"· **「收网改成共 {total_if_plus} 份」**\n\n"
        f"若本意就是累计 **{int(stated_cumulative_target)}** 份即可，则已超过，可先 **「分析简历」** 或再说新目标。"
    )


def send_hr_incremental_resume_target_clarify_if_configured(
    *,
    pending_count: int,
    stated_cumulative_target: int,
    job_title: str = "",
) -> dict[str, Any]:
    """仅当 pending 已严格大于刚写入的累计上限时发送，避免「还差几份」场景刷屏。"""
    if int(pending_count) <= int(stated_cumulative_target):
        return {"ok": False, "skipped": True, "reason": "pending_not_above_cap"}
    text = build_hr_incremental_resume_target_clarify_feishu_text(
        pending_count=pending_count,
        stated_cumulative_target=stated_cumulative_target,
        job_title=job_title,
    )
    tech = json.dumps(
        {
            "kind": "hr_resume_target_clarify",
            "pending_count": int(pending_count),
            "stated_cumulative_target": int(stated_cumulative_target),
            "job_title": (job_title or "").strip(),
        },
        ensure_ascii=False,
    )
    return send_hr_recruitment_progress_message(
        text,
        technical_detail=tech,
        message_kind="hr_resume_target_clarify",
    )


def format_hr_recruitment_progress_line_for_lark(job_name: str = "", job_folder: str = "") -> str:
    """
    一行**给人看**的简历进度（飞书用）。技术口径见日志。
    job_folder：与调度器持久化的数据目录键一致时传入，避免与指针串岗。
    """
    try:
        from l3_node.hr_loader import get_recruitment_scheduler

        rs = get_recruitment_scheduler()
        if rs is None or not hasattr(rs, "get_harvest_progress_snapshot"):
            return ""
        jn = (job_name or "").strip()
        jf = (job_folder or "").strip()
        if not jn or not jf:
            try:
                from l3_node.local_memory import get_hr_recruitment_workflow_pointer

                ptr = get_hr_recruitment_workflow_pointer()
                if not jn:
                    jn = (ptr.get("job_name") or "").strip()
                if not jf:
                    jf = (ptr.get("primary_job_folder") or ptr.get("job_folder") or "").strip()
            except Exception:
                pass
        n, cap = rs.get_harvest_progress_snapshot(jn, job_folder=jf)
        logger.debug(
            "[Lark HR] 简历进度技术口径 job=%s folder=%s pending_pdf=%s resume_collect_target=%s",
            jn or "(指针)",
            (jf or "(自动)")[:48],
            n,
            cap,
        )
        if cap <= 0 and n <= 0:
            return ""
        if cap <= 0:
            return f"已收到简历 **{n}** 份（尚未设置「共要收多少份」的上限，可在群里说「再抓 N 份」）"
        if n > cap:
            total_if_meant_incremental = n + cap
            return (
                f"pending 累计 **{n}** 份，**累计目标** **{cap}** 份（已超过；"
                f"该数字同时是 **收网上限** 与 **自动透析触发份数**，已写在 jd.json）。\n"
                f"若 **{cap}** 是指「**在现有 {n} 份上再收 {cap} 份**」，累计约 **{total_if_meant_incremental}** 份，"
                f"请发 **「再抓 {cap} 份」** 或 **「收网改成共 {total_if_meant_incremental} 份」**；"
                f"若本意就是累计 **{cap}** 份即可，可先 **「分析简历」** 或再说新目标。"
            )
        if n == cap:
            return (
                f"简历进度：**{n}/{cap}** 份（已达累计目标；收网与透析阈值同一上限；要再收可说「再抓 N 份」）"
            )
        return f"简历进度：**{n}/{cap}** 份（还在往目标收）"
    except Exception as e:
        logger.debug("[Lark HR] 格式化进度失败: %s", e)
        return ""


def build_hr_l3_status_briefing_text(*, reason: str = "startup") -> str:
    """
    L3 重启 / IM 重连 / HR 主动查询时，发飞书的**短简报**（人话）。

    分层：L1 当前指针岗位 + 一句话状态；L2 未完成优先；L3 最近其它岗；L4 可发指令。
    完整调度 digest 写入 logger。
    """
    if reason == "startup":
        head = "【招聘助手已上线】"
    elif reason == "reconnect":
        head = "【飞书已重新连上】"
    else:
        head = "【招聘进度一览】"

    def _suspended_scheduler_lines() -> list[str]:
        if rs is None or not hasattr(rs, "list_scheduler_suspended_jobs"):
            return []
        try:
            items = rs.list_scheduler_suspended_jobs()
        except Exception as ex:
            logger.debug("[Lark HR] list_scheduler_suspended_jobs 跳过: %s", ex)
            return []
        if not items:
            return []
        parts: list[str] = []
        for it in items[:5]:
            jn = str(it.get("job_name") or it.get("job_folder") or "?").strip()
            jf = str(it.get("job_folder") or "").strip()
            if jf:
                parts.append(f"「{jn}」(`{jf}`)")
            else:
                parts.append(f"「{jn}」")
        tail = f"（共 **{len(items)}** 项）" if len(items) > 5 else ""
        return [
            f"· **挂起可恢复（换岗抢占）**：{', '.join(parts)}{tail} — 发 **恢复挂起岗位：`目录键`** 或让助手调 MCP **resume_hr_job_scheduler**"
        ]

    def _l4_common_block(*, has_bound_job: bool) -> list[str]:
        cont = (
            "· **继续** — 全局暂停后恢复自动跑；若尚未绑定岗，也可先试接上指针里上次的岗"
            if not has_bound_job
            else "· **继续** — 全局暂停后恢复自动跑"
        )
        lines = [
            "**L4｜可以发**",
            cont,
            "· **恢复挂起岗位：`目录键`** — 换回之前因换岗被卸掉的无人值守（与当前岗互斥）",
            "· **同意调度** — 刚收到参数确认单后，确认才开始定时",
            "· **继续仅打招呼** / **仅打招呼 N 重开** — 有未完成的「仅打招呼」或要改目标重来",
            "· **进度** — 查当前岗简历收到多少、离目标差几份",
            "· **绑定 / 换岗** — 直接说要招什么岗，或一行「岗位 + 城市 + 薪资」",
            "· **仅收网** / **只抓简历** / **关闭打招呼** — 关推荐牛人，只定时从沟通里收简历（整句匹配）",
            "· **收网改成 80 人** / **打招呼改成 5 人** / **推荐间隔 15 分钟** — 交替模式下：每轮左侧会话上限、每轮打招呼上限、推荐↔收简历轮换分钟数（可写在同一条）",
            "· **一条改齐示例**：`打招呼改成10人 收网改成50人 推荐间隔2分钟`（作用在**当前指针岗**；与助手对话不是同一路）",
            "· **易混**：`收网改成N人` = 每轮 tick **最多处理 N 个左侧沟通会话**，不是「累计收满 N 份简历」；要多收简历发 **再抓 N 份** 或让助手改累计目标。",
            "· **仅打招呼20** — 累计成功打招呼 **20 次** 的独立战役；**打招呼改成20人** 是交替里**每轮**上限，二者不同。",
            "· **分析简历** — 登记优先透析（够阈值会集中跑）",
        ]
        lines.extend(
            [
                "",
                "**流程提醒**：① 推荐里联系 → ② 沟通里收简历 → ③ AI 评价与同步飞书表（若已配置）",
                "说明：带「改成」「间隔」「仅收网」「再抓」的短句由 **L3 拦截器直接改参并重注册**，与纯自然语言找助手不是同一路。",
            ]
        )
        return lines

    try:
        from l3_node.hr_loader import get_recruitment_scheduler
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        ptr = get_hr_recruitment_workflow_pointer()
        if not isinstance(ptr, dict):
            ptr = {}
        rs = get_recruitment_scheduler()

        if rs is None or not hasattr(rs, "get_recruitment_status_digest"):
            out = [
                head,
                "",
                "**L1｜当前岗位**",
                "读不到招聘调度模块（未加载或导入失败），无法判断跑/停。",
                "",
            ]
            recent = _collect_recent_other_jobs(exclude_jf="", exclude_title="", rs=None)
            if recent:
                out.extend(["**L3｜最近还动过的岗位**", *recent, ""])
            out.extend(_l4_common_block(has_bound_job=False))
            return "\n".join(out)

        d = rs.get_recruitment_status_digest("")
        logger.info(
            "[Lark HR] 上线简报技术 digest=%s",
            json.dumps(d, ensure_ascii=False, default=str)[:12000],
        )

        if not d.get("has_active_job"):
            l2: list[str] = []
            if ptr.get("scheduler_pending_confirm"):
                l2.append(
                    "· 指针仍标记 **待飞书确认调度** — 发 **同意调度**，或先绑定岗位后再确认"
                )
            inc = None
            if hasattr(rs, "incomplete_greet_only_for_pointer"):
                try:
                    inc = rs.incomplete_greet_only_for_pointer()
                except Exception:
                    inc = None
            if isinstance(inc, dict) and inc.get("target"):
                l2.append(
                    f"· **仅打招呼**上次未跑满：**{inc.get('done')}/{inc.get('target')}** — 可 **继续仅打招呼** 或 **仅打招呼{inc.get('target')}重开**"
                )
            l2.extend(_suspended_scheduler_lines())
            l2_block = (
                "\n".join(["**L2｜待接续 / 未完成**", *l2])
                if l2
                else "**L2｜待接续 / 未完成**\n· （暂无指针岗位上的未完成项；绑定后会显示收网、仅打招呼、待透析等）"
            )
            recent = _collect_recent_other_jobs(exclude_jf="", exclude_title="", rs=rs)
            l3_block = ""
            if recent:
                l3_block = "\n".join(["", "**L3｜最近还动过的岗位**", *recent])
            return "\n".join(
                [
                    head,
                    "",
                    "**L1｜当前岗位**：暂无（未绑定具体岗位）",
                    l2_block,
                    l3_block,
                    "",
                    *_l4_common_block(has_bound_job=False),
                ]
            )

        jn = d.get("job_name") or "当前岗位"
        jf = (d.get("job_folder") or "").strip()
        n = int(d.get("pending_pdf_count") or 0)
        cap = int(d.get("collect_cap") or 0)
        unproc = int(d.get("unprocessed_for_analysis") or 0)
        thr = int(d.get("analyze_threshold") or 4)
        gt = int(d.get("greet_target") or 3)
        rim = int(d.get("recommend_interval_minutes") or 15)
        sw = int(d.get("greet_harvest_switch_interval_minutes") or rim or 10)
        eg = bool(d.get("enable_greet_recommend", True))
        sched_on = bool(d.get("scheduler_active"))
        stopped = bool(d.get("globally_stopped"))
        manual = bool(d.get("manual_analyze_pending"))
        greet_only_live = bool(d.get("greet_only_scheduler_active"))
        got = int(d.get("greet_only_total_target") or 0)
        gdone = int(d.get("greet_only_done") or 0)
        pending_confirm = bool(ptr.get("scheduler_pending_confirm"))

        if stopped:
            stage_one = "**停**：全局已暂停，不会自动打招呼、收简历"
        elif greet_only_live and not sched_on:
            stage_one = f"**跑（仅打招呼）**：定时进行中 **{gdone}/{got}**，主交替调度未开"
        elif sched_on:
            stage_one = "**跑**：主调度在跑（打招呼/收网按设定交替）"
        else:
            stage_one = "**未开调度**：定时未在跑（常见：刚上线或待确认）"

        l1 = "\n".join(
            [
                f"**L1｜当前岗位**：**{jn}**",
                f"· 一句话状态：{stage_one}",
            ]
        )

        l2_items: list[str] = []
        inc = None
        if hasattr(rs, "incomplete_greet_only_for_pointer"):
            try:
                inc = rs.incomplete_greet_only_for_pointer()
            except Exception:
                inc = None
        if isinstance(inc, dict) and inc.get("target"):
            l2_items.append(
                f"· **仅打招呼**未完成：**{inc.get('done')}/{inc.get('target')}**（定时已停）— **继续仅打招呼** / **仅打招呼{inc.get('target')}重开**"
            )
        if cap > 0 and n < cap:
            l2_items.append(
                f"· **收网未达目标**：简历 **{n}/{cap}** 份，还差 **{cap - n}** 份"
            )
        elif cap > 0 and n > cap:
            l2_items.append(
                f"· **pending 超过累计目标**：**{n}** 份 / 目标 **{cap}** 份（收网与自动透析同一上限）— "
                f"若 **{cap}** 是「再多收」而非「一共」，请 **「再抓 {cap} 份」** 或 **「收网改成共 {n + cap} 份」**"
            )
        if pending_confirm and not sched_on:
            l2_items.append(
                "· **待确认调度**：飞书若已收到参数单，发 **同意调度** 后才会自动跑"
            )
        if unproc > 0:
            if cap > 0:
                l2_items.append(
                    f"· **待透析堆积**：约 **{unproc}** 份在排队（满 **{thr}** 份集中跑一轮；"
                    f"阈值与累计收网目标 **{cap}** 为同一口径）"
                )
            else:
                l2_items.append(
                    f"· **待透析堆积**：约 **{unproc}** 份在排队（满 **{thr}** 份会集中跑一轮）"
                )
        elif manual:
            l2_items.append("· 已登记 **分析简历**，有简历时会优先安排透析")
        if greet_only_live:
            l2_items.append(
                f"· **仅打招呼**定时**正在跑**：**{gdone}/{got}**"
            )

        l2_items.extend(_suspended_scheduler_lines())

        if not l2_items:
            l2_items.append("· （暂无明确未完成项；一切按设定推进中）")

        line_harvest = format_hr_recruitment_progress_line_for_lark(jn, job_folder=jf) or (
            f"简历：**{n}/{cap}** 份" if cap > 0 else f"已收简历 **{n}** 份"
        )
        if eg:
            rule_one = (
                f"单页严格交替；轮换约 **{sw}** 分钟；每轮打招呼最多 **{gt}** 人"
            )
        else:
            rule_one = f"已关自动打招呼，约每 **{sw}** 分钟收沟通里简历"

        l2_block = "\n".join(["**L2｜待接续 / 未完成**", *l2_items])

        recent = _collect_recent_other_jobs(
            exclude_jf=jf, exclude_title=jn, rs=rs
        )
        l3_block = ""
        if recent:
            l3_block = "\n".join(["", "**L3｜最近还动过的其他岗位**", *recent])

        harvest_reached = cap > 0 and n >= cap
        batch_note = ""
        if harvest_reached and unproc <= 0 and n == 0:
            batch_note = "\n· 本批收满且透析已齐的迹象；要再招可说「再抓几份」或换岗。"
        elif harvest_reached and n > cap:
            batch_note = (
                f"\n· pending **{n}** 份已超过累计目标 **{cap}**（与透析触发阈值一致）；"
                f"进度条里已说明「累计 vs 再收」可选说法。"
            )

        wid = (ptr.get("workflow_id") or "").strip()
        if wid:
            logger.debug("[Lark HR] workflow_id=%s（仅日志）", wid)

        return "\n".join(
            [
                head,
                "",
                l1,
                f"· {line_harvest}",
                f"· 规则摘要：{rule_one}",
                l2_block,
                l3_block,
                batch_note,
                "",
                *_l4_common_block(has_bound_job=True),
            ]
        )
    except Exception as e:
        logger.debug("[Lark HR] 构建上线简报失败: %s", e)
        return f"{head}\n\n简报生成出了点问题，请直接说您想「继续收简历」还是「分析简历」。\n（技术同事可看日志：{e}）"


def send_hr_l3_online_briefing_if_configured(*, reason: str = "startup") -> dict[str, Any]:
    """
    向当前 HR 飞书会话发送上线/重连简报（与进度推送共用 chat_id 逻辑）。
    环境变量 JACHIN_HR_LARK_BRIEF_ON_START=0|false|off 可关闭「启动时」自动发送。
    """
    if reason == "startup":
        v = (os.environ.get("JACHIN_HR_LARK_BRIEF_ON_START") or "1").strip().lower()
        if v in ("0", "false", "no", "off"):
            return {"ok": False, "skipped": True, "reason": "JACHIN_HR_LARK_BRIEF_ON_START off"}

    text = build_hr_l3_status_briefing_text(reason=reason)
    tech = ""
    try:
        from l3_node.hr_loader import get_recruitment_scheduler

        rs = get_recruitment_scheduler()
        if rs is not None and hasattr(rs, "get_recruitment_status_digest"):
            tech = json.dumps(rs.get_recruitment_status_digest(""), ensure_ascii=False, default=str)[:12000]
    except Exception:
        pass
    r = send_hr_recruitment_progress_message(
        text,
        technical_detail=tech.strip() or None,
        message_kind="hr_briefing",
    )
    if r.get("skipped"):
        logger.info("[Lark HR] 上线简报未发送 reason=%s detail=%s", reason, r.get("reason", r))
    elif r.get("status") == "success":
        logger.info("[Lark HR] 已发送上线简报 reason=%s chat 已投递", reason)
    else:
        logger.warning("[Lark HR] 上线简报发送结果 reason=%s r=%s", reason, r)
    return r
