"""
L3 记忆宿主：核心写入已迁移 **Memory Nexus（SQLite + FastEmbed）**；**l3_local.json 仅保留读取/合并器/历史诊断**。

- 被动记忆读取统一由 Cognitive Kernel 的 `MemoryRecallAgent -> RelevantMemoryBundle` 完成。
- `add_local_memory` → `commit_drawer`（User_Persona / Learned_Skills）。
架构入口: docs/07_memory_first_main_agent_and_voice_app_agents.md；禁止旧被动记忆直接注入 prompt。
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JACHIN_ROOT = Path.home() / ".jachin"
_MEMORY_DIR = _JACHIN_ROOT / "memory"
_LOCAL_DB = _MEMORY_DIR / "l3_local.json"
_MAX_ENTRIES = 200  # 最多保留条数
# compaction 写入的会话摘要，常含「当时 workspace 里有哪些文件」等**易过期状态**；
# 注入 system 会导致新会话「记忆污染」、模型跳过 list_directory —— 仅保留在检索库里，不被动注入。
_TAGS_EXCLUDE_FROM_PASSIVE_PROMPT: frozenset[str] = frozenset({"task_checkpoint"})
_PROMPT_CYCLE = 0
_MEMORY_SHARD_ID: ContextVar[str | None] = ContextVar("l3_memory_shard_id", default=None)


def set_memory_shard_id_token(shard_id: str) -> object | None:
    s = (shard_id or "").strip()
    if not s:
        return None
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in s[:72])
    return _MEMORY_SHARD_ID.set(safe)


def reset_memory_shard_token(token: object | None) -> None:
    if token is not None:
        try:
            _MEMORY_SHARD_ID.reset(token)
        except ValueError:
            pass


def _db_path() -> Path:
    sid = _MEMORY_SHARD_ID.get()
    if sid:
        return _MEMORY_DIR / f"l3_local_shard_{sid}.json"
    return _LOCAL_DB


def load_raw_entries() -> list[dict]:
    """供 memory_facade / local_memory_search 读取当前 shard。"""
    return _load_raw()


def next_prompt_cycle() -> int:
    """单调递增的 prompt 轮次，用于被动记忆衰减（未注入超过 N 轮则不再塞进 prompt）。"""
    global _PROMPT_CYCLE
    _PROMPT_CYCLE += 1
    return _PROMPT_CYCLE


def _memory_passive_max_idle() -> int:
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        m = cfg.get("memory") or {}
        return max(1, int(m.get("passive_max_idle_runs", 12)))
    except Exception:
        return 12


def bump_memory_inject_cycle_for_content_hit(
    text_snippet: str,
    *,
    prompt_cycle: int,
    max_scan_chars: int = 400,
) -> None:
    """prefetch / 工具读到与某条记忆内容重叠的文本时，刷新该条的注入轮次，避免误衰减。"""
    snip = (text_snippet or "").strip()[:max_scan_chars]
    if not snip or len(snip) < 8:
        return
    entries = _load_raw()
    changed = False
    for e in entries:
        c = (e.get("content") or "").strip()
        if len(c) < 8:
            continue
        if snip in c or c[:80] in snip:
            e["last_prompt_inject_cycle"] = int(prompt_cycle)
            changed = True
    if changed:
        _save_raw(entries)


def _ensure_dir() -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_raw() -> list[dict]:
    """加载本地记忆 JSON"""
    _ensure_dir()
    p = _db_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug("[L3 LocalMemory] 加载失败: %s", e)
        return []


def _save_raw(entries: list[dict]) -> None:
    """保存本地记忆"""
    _ensure_dir()
    try:
        _db_path().write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[L3 LocalMemory] 保存失败: %s", e)


def add_local_memory(
    tag: str,
    content: str,
    *,
    source: str = "agent",
    tags_list: list[str] | None = None,
) -> bool:
    """
    写入一条核心记忆：**已迁移至 Memory Nexus（SQLite）** User_Persona / Learned_Skills。
    （历史 l3_local.json 写入路径已废弃，保留 load 仅兼容旧 compaction/诊断。）

    tag: preference | user_habit | config_hint | fact | task_checkpoint 等
    tags_list: 可选；写入 extra_meta。

    Returns:
        True 若已提交 Nexus；False 仅当参数无效（空 content/tag）。commit_drawer 失败时抛出异常（携带底层原因）。
    """
    content = (content or "").strip()
    tag = (tag or "general").strip()
    if not content or not tag:
        return False
    extra_meta: dict[str, Any] = {"tag": tag, "source": source}
    if tags_list:
        clean = [str(t).strip() for t in tags_list if str(t).strip()][:32]
        if clean:
            extra_meta["tags_json"] = json.dumps(clean, ensure_ascii=False)
    try:
        from l3_client.local_mcps.jachin_memory_nexus.memory_backend import commit_drawer

        commit_drawer(
            text=f"[{tag}] {content}",
            wing="User_Persona",
            room="Learned_Skills",
            extra_meta=extra_meta,
        )
    except Exception as e:
        logger.warning(
            "[L3 LocalMemory] Memory Nexus commit_drawer 失败，底层: %s",
            e,
            exc_info=True,
        )
        raise
    logger.debug("[L3 LocalMemory] 已写入 Nexus tag=%s", tag)
    return True


def touch_entries_from_search_hits(hits: list[dict]) -> None:
    """Memory Nexus 迁移后检索不再写回 l3_local.json；保留 API 以利旧调用链 no-op。"""
    if not hits:
        return
    return


def get_local_memory_for_prompt(
    limit: int = 15,
    *,
    prompt_cycle: int | None = None,
    max_idle_prompt_cycles: int | None = None,
) -> str:
    """
    兼容旧 API：被动记忆 prompt 快照已停用。

    所有主循环记忆读取必须通过 MemoryRecallAgent 进入 RelevantMemoryBundle。
    limit / prompt_cycle 等参数保留签名兼容，但不再触发 Memory Nexus 读取。
    """
    return ""


def merge_from_l2(items: list[dict]) -> None:
    """
    已移除：L3 记忆不再经 L2 回灌；跨会话 SSOT 为 Memory Nexus（SQLite + FastEmbed）。
    保留空实现以免旧调用链 import 报错。
    """
    if not items:
        return
    logger.debug("[L3 LocalMemory] merge_from_l2 已移除（L3-only 记忆），忽略 %d 条", len(items))


# -----------------------------------------------------------------------------
# DAG Workflow 状态持久（供 core/workflow_engine 断点续传）
# -----------------------------------------------------------------------------

_WORKFLOW_STATES = _MEMORY_DIR / "workflow_states.json"


def save_workflow_state(workflow_id: str, state: dict) -> None:
    """保存 DAG Workflow 状态，供断点续传。"""
    _ensure_dir()
    try:
        all_states: dict = {}
        if _WORKFLOW_STATES.exists():
            all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
            if not isinstance(all_states, dict):
                all_states = {}
        all_states[workflow_id] = {**state, "workflow_id": workflow_id}
        _WORKFLOW_STATES.write_text(json.dumps(all_states, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("[L3 LocalMemory] 已保存 workflow 状态: %s", workflow_id)
    except Exception as e:
        logger.warning("[L3 LocalMemory] 保存 workflow 状态失败: %s", e)


def load_workflow_state(workflow_id: str) -> dict | None:
    """加载 DAG Workflow 状态，不存在返回 None。"""
    if not _WORKFLOW_STATES.exists():
        return None
    try:
        all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
        if not isinstance(all_states, dict):
            return None
        return all_states.get(workflow_id)
    except Exception as e:
        logger.debug("[L3 LocalMemory] 加载 workflow 状态失败: %s", e)
        return None


def delete_workflow_state(workflow_id: str) -> bool:
    """删除指定 workflow 状态（完成后清理）。"""
    if not _WORKFLOW_STATES.exists():
        return True
    try:
        all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
        if isinstance(all_states, dict) and workflow_id in all_states:
            del all_states[workflow_id]
            _WORKFLOW_STATES.write_text(json.dumps(all_states, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("[L3 LocalMemory] 删除 workflow 状态失败: %s", e)
        return False


def list_workflow_state_ids() -> list[str]:
    """列出已持久化的 workflow_id（供 Lark 分析指令选择目标）。"""
    if not _WORKFLOW_STATES.exists():
        return []
    try:
        all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
        if not isinstance(all_states, dict):
            return []
        return list(all_states.keys())
    except Exception as e:
        logger.debug("[L3 LocalMemory] 列举 workflow 失败: %s", e)
        return []


# -----------------------------------------------------------------------------
# HR 招聘：当前 DAG / 收网任务指针（供 Lark 停止、分析指令解析 workflow_id）
# -----------------------------------------------------------------------------

_HR_WF_POINTER = _MEMORY_DIR / "hr_recruitment_workflow_pointer.json"


def _hr_fallback_job_folder(job_name: str) -> str:
    """与招聘调度器目录规则近似：岗位名 → 安全文件夹名。"""
    illegal = r'\/:*?"<>|'
    s = (job_name or "").strip()
    for c in illegal:
        s = s.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in s)
    return (s.strip("_")[:60] or "未命名")


def _normalize_hr_pointer_dict(raw: dict) -> dict:
    """补全 jobs / primary_job_folder / job_folder，兼容旧版仅顶层字段的指针文件。"""
    data = dict(raw)
    jobs_in = data.get("jobs")
    jobs: list[dict] = []
    if isinstance(jobs_in, list):
        for x in jobs_in:
            if isinstance(x, dict):
                jobs.append(dict(x))
    jn = (data.get("job_name") or "").strip()
    jdp = (data.get("jd_config_path") or "").strip()
    rpd = (data.get("resume_pending_dir") or "").strip()
    wid = (data.get("workflow_id") or "").strip()
    pjf = (data.get("primary_job_folder") or data.get("job_folder") or "").strip()
    if not jobs and (jn or jdp or wid):
        jf = pjf or _hr_fallback_job_folder(jn) if jn else pjf
        if not jf and jn:
            jf = _hr_fallback_job_folder(jn)
        if jf or jn:
            jobs.append(
                {
                    "job_folder": jf or jn or "未命名",
                    "job_name": jn or jf or "未命名",
                    "jd_config_path": jdp,
                    "resume_pending_dir": rpd,
                    "workflow_id": wid,
                    "updated_at": float(data.get("updated_at") or 0),
                }
            )
    data["jobs"] = jobs
    if not pjf and jobs:
        pjf = (jobs[-1].get("job_folder") or jobs[-1].get("job_name") or "").strip()
    if pjf:
        data["primary_job_folder"] = pjf
        data["job_folder"] = pjf
    elif jn:
        data["job_folder"] = _hr_fallback_job_folder(jn)
    return data


def set_hr_recruitment_workflow_pointer(
    workflow_id: str,
    *,
    job_name: str = "",
    job_folder: str = "",
    jd_config_path: str = "",
    resume_pending_dir: str = "",
    lark_chat_id: str | None = None,
    scheduler_pending_confirm: bool | None = None,
) -> None:
    """
    记录当前 HR 招聘 DAG 或收网任务关联信息；并在 ``jobs`` 中按 job_folder 去重登记多岗。
    lark_chat_id：传入非 None 时更新飞书会话 ID（用于进度推送）；传 None 表示保留原值。
    scheduler_pending_confirm：True/False 显式更新「已发飞书待 HR 回复同意调度」标记；None 表示保留指针原值。
    其他字段若为空字符串则尽量保留指针中已有值，避免调度器同步时冲掉 chat_id。
    """
    _ensure_dir()
    try:
        prev = get_hr_recruitment_workflow_pointer()
        wid = (workflow_id or "").strip() or (prev.get("workflow_id") or "")
        jn = (job_name or "").strip() or (prev.get("job_name") or "")
        jdp = (jd_config_path or "").strip() or (prev.get("jd_config_path") or "")
        rpd = (resume_pending_dir or "").strip() or (prev.get("resume_pending_dir") or "")
        if lark_chat_id is not None:
            lc = (lark_chat_id or "").strip()
        else:
            lc = (prev.get("lark_chat_id") or "").strip()

        only_chat = (
            lark_chat_id is not None
            and not (job_folder or "").strip()
            and not (job_name or "").strip()
            and not (jd_config_path or "").strip()
            and not (resume_pending_dir or "").strip()
            and not (workflow_id or "").strip()
            and scheduler_pending_confirm is None
        )
        if only_chat:
            data = {**prev, "lark_chat_id": lc, "updated_at": time.time()}
            _HR_WF_POINTER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return

        jf_key = (job_folder or "").strip()
        if not jf_key and jdp:
            try:
                from pathlib import Path

                jp = Path(jdp.strip()).resolve()
                if jp.is_file() and jp.name.lower() == "jd.json":
                    jf_key = jp.parent.name
            except OSError:
                pass
        if not jf_key and jn:
            jf_key = _hr_fallback_job_folder(jn)
        if not jf_key:
            jf_key = (prev.get("primary_job_folder") or prev.get("job_folder") or "").strip()
        if not jf_key and isinstance(prev.get("jobs"), list) and prev["jobs"]:
            last = prev["jobs"][-1]
            if isinstance(last, dict):
                jf_key = (last.get("job_folder") or "").strip()

        jobs: list[dict] = []
        for x in prev.get("jobs") or []:
            if isinstance(x, dict):
                jobs.append(dict(x))

        now = time.time()
        display_name = jn or jf_key or (prev.get("job_name") or "") or "未命名"
        if jf_key:
            entry = {
                "job_folder": jf_key,
                "job_name": display_name,
                "jd_config_path": jdp,
                "resume_pending_dir": rpd,
                "workflow_id": wid,
                "updated_at": now,
            }
            jobs = [x for x in jobs if (x.get("job_folder") or "").strip() != jf_key]
            jobs.append(entry)
            jobs.sort(key=lambda x: float(x.get("updated_at") or 0), reverse=True)
            jobs = jobs[:25]

        data: dict = {
            "workflow_id": wid,
            "job_name": display_name if jf_key else (jn or prev.get("job_name") or ""),
            "jd_config_path": jdp,
            "resume_pending_dir": rpd,
            "lark_chat_id": lc,
            "updated_at": now,
            "jobs": jobs,
        }
        if jf_key:
            data["primary_job_folder"] = jf_key
            data["job_folder"] = jf_key
        else:
            if prev.get("primary_job_folder"):
                data["primary_job_folder"] = prev["primary_job_folder"]
            if prev.get("job_folder"):
                data["job_folder"] = prev["job_folder"]

        if scheduler_pending_confirm is not None:
            data["scheduler_pending_confirm"] = bool(scheduler_pending_confirm)
        elif "scheduler_pending_confirm" in prev:
            data["scheduler_pending_confirm"] = prev["scheduler_pending_confirm"]

        data = _normalize_hr_pointer_dict(data)
        _HR_WF_POINTER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if jf_key:
            try:
                from l3_node.hr_loader import hr_set_jd_show_in_hr_briefing_for_folder

                hr_set_jd_show_in_hr_briefing_for_folder(jf_key, True)
            except Exception as _e:
                logger.debug("[L3 LocalMemory] 绑定岗后写 jd show_in_hr_briefing 跳过: %s", _e)
        logger.debug("[L3 LocalMemory] HR workflow 指针已更新: %s chat=%s jobs=%d", data["workflow_id"], "有" if lc else "无", len(data.get("jobs") or []))
    except Exception as e:
        logger.warning("[L3 LocalMemory] 写入 HR workflow 指针失败: %s", e)


def get_hr_recruitment_workflow_pointer() -> dict:
    """读取 HR 招聘 workflow 指针；不存在返回空 dict。返回前补全 jobs 列表（兼容旧文件）。"""
    if not _HR_WF_POINTER.exists():
        return {}
    try:
        data = json.loads(_HR_WF_POINTER.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return _normalize_hr_pointer_dict(data)
    except Exception as e:
        logger.debug("[L3 LocalMemory] 读取 HR workflow 指针失败: %s", e)
        return {}


def list_hr_recruitment_job_entries() -> list[dict]:
    """多岗登记列表（每项含 job_folder、job_name、jd_config_path 等），按 updated_at 新→旧。"""
    ptr = get_hr_recruitment_workflow_pointer()
    jobs = ptr.get("jobs")
    if not isinstance(jobs, list):
        return []
    out: list[dict] = []
    for x in jobs:
        if isinstance(x, dict):
            out.append(dict(x))
    return out


def _hr_pointer_job_matches_query(entry: dict, query: str) -> bool:
    q = (query or "").strip()
    if not q or not isinstance(entry, dict):
        return False
    jn = (entry.get("job_name") or "").strip()
    jf = (entry.get("job_folder") or "").strip()
    qf = _hr_fallback_job_folder(q)
    if qf and (_hr_fallback_job_folder(jn) == qf or _hr_fallback_job_folder(jf) == qf):
        return True
    if len(q) >= 2 and (q in jn or q in jf):
        return True
    return False


def remove_hr_recruitment_job_from_pointer(query: str) -> tuple[bool, str, str]:
    """
    从 ``jobs`` 中移除与 ``query`` 匹配的岗位登记（sanitize 相等或子串匹配）。

    返回 ``(ok, message, removed_job_name)``；未匹配或歧义时 ``removed_job_name`` 为空。
    """
    q = (query or "").strip()
    if not q:
        return False, "请说明要清除的岗位名。", ""
    prev = get_hr_recruitment_workflow_pointer()
    jobs_in = prev.get("jobs")
    if not isinstance(jobs_in, list) or not jobs_in:
        return False, "指针里没有登记过的岗位。", ""

    matches: list[dict] = []
    for x in jobs_in:
        if isinstance(x, dict) and _hr_pointer_job_matches_query(x, q):
            matches.append(x)

    if not matches:
        return False, f"未找到与「{q}」匹配的岗位（可看简报里【其他岗位】全名后再试）。", ""

    if len(matches) > 1:
        names = "、".join((m.get("job_name") or m.get("job_folder") or "?") for m in matches[:6])
        return False, f"「{q}」匹配到多个岗位，请说完整岗位名：{names}", ""

    victim = matches[0]
    removed_name = (victim.get("job_name") or victim.get("job_folder") or "").strip()

    def _entry_key(e: dict) -> tuple:
        return (
            (e.get("job_folder") or "").strip(),
            (e.get("job_name") or "").strip(),
            float(e.get("updated_at") or 0),
        )

    vkey = _entry_key(victim)
    remaining = [dict(x) for x in jobs_in if isinstance(x, dict) and _entry_key(x) != vkey]

    lc = (prev.get("lark_chat_id") or "").strip()
    now = time.time()

    if not remaining:
        data: dict = {
            "lark_chat_id": lc,
            "jobs": [],
            "updated_at": now,
        }
        if "scheduler_pending_confirm" in prev:
            data["scheduler_pending_confirm"] = prev["scheduler_pending_confirm"]
        _ensure_dir()
        _HR_WF_POINTER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[L3 LocalMemory] 已清除全部岗位指针登记（保留 lark_chat_id）")
        return True, f"已移除岗位「{removed_name}」；指针中已无其他岗位。", removed_name

    remaining.sort(key=lambda x: float(x.get("updated_at") or 0), reverse=True)
    top = remaining[0]
    jn_t = (top.get("job_name") or "").strip()
    jf_t = (top.get("job_folder") or "").strip()
    data = {
        "workflow_id": (top.get("workflow_id") or prev.get("workflow_id") or "").strip(),
        "job_name": jn_t or jf_t,
        "jd_config_path": (top.get("jd_config_path") or "").strip(),
        "resume_pending_dir": (top.get("resume_pending_dir") or "").strip(),
        "lark_chat_id": lc,
        "primary_job_folder": jf_t,
        "job_folder": jf_t,
        "jobs": remaining,
        "updated_at": now,
    }
    if "scheduler_pending_confirm" in prev:
        data["scheduler_pending_confirm"] = prev["scheduler_pending_confirm"]
    data = _normalize_hr_pointer_dict(data)
    _ensure_dir()
    _HR_WF_POINTER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[L3 LocalMemory] 已从指针移除岗位=%s，当前主岗=%s", removed_name, jn_t or jf_t)
    return True, f"已清除岗位「{removed_name}」的指针记忆；当前主岗已切到「{jn_t or jf_t}」。", removed_name


def clear_all_hr_recruitment_pointer(*, keep_lark_chat: bool = True) -> None:
    """清空 ``hr_recruitment_workflow_pointer.json`` 中的岗位列表与主岗字段；默认保留飞书 chat_id。"""
    prev = get_hr_recruitment_workflow_pointer()
    lc = (prev.get("lark_chat_id") or "").strip() if keep_lark_chat else ""
    data: dict = {
        "jobs": [],
        "updated_at": time.time(),
    }
    if lc:
        data["lark_chat_id"] = lc
    _ensure_dir()
    _HR_WF_POINTER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[L3 LocalMemory] 已清空 HR 招聘指针（保留 chat=%s）", "是" if lc else "否")


def get_hr_recruitment_active_workflow_id() -> str | None:
    """当前 Lark/调度应操作的 hr_recruitment workflow_id。"""
    ptr = get_hr_recruitment_workflow_pointer()
    wid = (ptr.get("workflow_id") or "").strip()
    return wid or None


def clear_stop_harvest_from_workflow_state(workflow_id: str) -> int:
    """
    从持久化 workflow state 的 _workflow_signals 队列中移除 STOP_HARVEST。
    返回移除条数（供飞书「继续」与调度恢复）。
    """
    wid = (workflow_id or "").strip()
    if not wid:
        return 0
    stop = "STOP_HARVEST"
    saved = load_workflow_state(wid)
    if not saved:
        return 0
    st = saved.get("state")
    if not isinstance(st, dict):
        return 0
    raw_q = st.get("_workflow_signals") or []
    if not isinstance(raw_q, list):
        return 0
    q = [x for x in raw_q if str(x).strip() != stop]
    removed = len(raw_q) - len(q)
    if removed <= 0:
        return 0
    st = {**st, "_workflow_signals": q}
    save_workflow_state(wid, {**saved, "state": st})
    logger.info("[L3 LocalMemory] 已从 workflow=%s 持久化信号中移除 STOP_HARVEST x%d", wid, removed)
    return removed


def main_local_memory_json_path() -> Path:
    """主会话 `l3_local.json` 绝对路径（非 delegate 分片）；遗留诊断/少数仍读 JSON 的路径；**非** Memory Nexus 主存储。"""
    return _LOCAL_DB.expanduser().resolve()
