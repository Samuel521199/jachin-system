"""
L4 动态经验飞轮（Experience RAG-lite）：本地 JSONL + 纯标准库相似度检索，无向量库、无 numpy。

架构 SSOT：docs/architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md §6。

- 默认存储：~/.jachin/workspace/.jachin_experience.jsonl（可用 JACHIN_EXPERIENCE_JSONL 覆盖）
- 写入：仅在 SQLite read_query/write_query + Critic 已通过（或关闭）+ Observation 未显式失败时由 agent_core 触发，避免毒化经验池。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def experience_rag_enabled() -> bool:
    v = (os.environ.get("JACHIN_EXPERIENCE_RAG_ENABLED") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _experience_path() -> Path:
    raw = (os.environ.get("JACHIN_EXPERIENCE_JSONL") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".jachin" / "workspace" / ".jachin_experience.jsonl").expanduser().resolve()


def _similarity_threshold() -> float:
    try:
        v = float(os.environ.get("JACHIN_EXPERIENCE_SIM_THRESHOLD") or "0.4")
        return max(0.05, min(0.99, v))
    except (TypeError, ValueError):
        return 0.4


def _max_file_lines() -> int:
    try:
        return max(50, min(20_000, int(os.environ.get("JACHIN_EXPERIENCE_MAX_LINES") or "2000")))
    except (TypeError, ValueError):
        return 2000


def _tokenize(text: str) -> list[str]:
    s = (text or "").lower()
    return re.findall(r"[\w\u4e00-\u9fff]{2,}", s, flags=re.UNICODE)


def _intent_similarity(query: str, historical_intent: str) -> float:
    """
    综合整句 SequenceMatcher 与分词集合 Jaccard，取较大值作为相似度（0~1）。
    短中文查询上 Jaccard 更稳；整句 ratio 对语序相近的复述更敏感。
    """
    q, h = (query or "").strip(), (historical_intent or "").strip()
    if not q or not h:
        return 0.0
    q_l, h_l = q.lower(), h.lower()
    seq = SequenceMatcher(None, q_l, h_l).ratio()
    tq, th = set(_tokenize(q)), set(_tokenize(h))
    if not tq or not th:
        return seq
    jacc = len(tq & th) / len(tq | th)
    return max(seq, jacc)


def _normalize_record(obj: dict[str, Any]) -> dict[str, Any] | None:
    """统一为 user_intent + executed_tool + action_payload。"""
    ui = str(obj.get("user_intent") or "").strip()
    if not ui:
        return None
    et = str(obj.get("executed_tool") or "").strip()
    ap = obj.get("action_payload")
    if et and isinstance(ap, dict):
        return {"user_intent": ui, "executed_tool": et, "action_payload": ap, "ts": obj.get("ts")}
    ca = obj.get("correct_action")
    if isinstance(ca, dict):
        tid = str(ca.get("tool_id") or "").strip()
        if tid:
            inp = ca.get("action_input")
            payload: dict[str, Any]
            if isinstance(inp, dict):
                payload = dict(inp)
            else:
                payload = {"action_input": str(inp or "")}
            return {"user_intent": ui, "executed_tool": tid, "action_payload": payload, "ts": obj.get("ts")}
    return None


def _load_records_unlocked(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("[ExperienceRAG] 读取失败（静默跳过）: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        norm = _normalize_record(obj)
        if norm:
            out.append(norm)
    return out


def retrieve_experience(user_intent: str, top_k: int = 2) -> list[dict[str, Any]]:
    """
    读取 JSONL，按意图相似度排序；仅保留相似度 >= 阈值（默认 0.4）的记录，返回前 top_k 条。
    读取/解析失败返回 []，不抛异常。
    """
    if not experience_rag_enabled():
        return []
    path = _experience_path()
    try:
        with _LOCK:
            recs = _load_records_unlocked(path)
    except Exception as e:
        logger.debug("[ExperienceRAG] retrieve 锁/读失败（静默跳过）: %s", e)
        return []
    if not recs:
        return []
    q = (user_intent or "").strip()
    if not q:
        return []
    thr = _similarity_threshold()
    scored: list[tuple[float, dict[str, Any]]] = []
    try:
        for r in recs:
            doc = str(r.get("user_intent") or "")
            sc = _intent_similarity(q, doc)
            if sc >= thr:
                scored.append((sc, r))
        scored.sort(key=lambda x: -x[0])
    except Exception as e:
        logger.debug("[ExperienceRAG] 评分失败（静默跳过）: %s", e)
        return []
    out: list[dict[str, Any]] = []
    k = max(1, min(8, int(top_k)))
    for _sc, row in scored[:k]:
        out.append(row)
    return out


def retrieve_relevant_experience(user_intent: str, *, top_k: int = 2) -> list[dict[str, Any]]:
    """兼容旧名；同 retrieve_experience。"""
    return retrieve_experience(user_intent, top_k=top_k)


def _format_payload_for_prompt(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)[:2000]
    except (TypeError, ValueError):
        return str(payload)[:2000]


def format_experience_block_for_prompt(user_intent: str, *, top_k: int = 2) -> str:
    """注入 system 的 [HISTORY_FEW_SHOTS]；无命中返回空串。"""
    rows = retrieve_experience(user_intent, top_k=top_k)
    if not rows:
        return ""
    lines: list[str] = [
        "[HISTORY_FEW_SHOTS]",
        "【历史成功经验库】：系统检索到过去处理类似任务的成功代码记录。请优先参考以下范例进行工具调用（表结构仍以当前轮 Probe 与语义层为准）：",
    ]
    for r in rows:
        ui = str(r.get("user_intent") or "").strip()
        tid = str(r.get("executed_tool") or "").strip()
        pl = r.get("action_payload")
        if not isinstance(pl, dict):
            pl = {}
        pay_s = _format_payload_for_prompt(pl)
        ui_show = ui[:600] + ("…" if len(ui) > 600 else "")
        lines.append(f"- 用户意图: {ui_show}")
        lines.append(f"  成功执行: 工具={tid}  payload={pay_s}")
    lines.append("[/HISTORY_FEW_SHOTS]")
    return "\n".join(lines)


def save_experience(user_intent: str, executed_tool: str, action_payload: dict[str, Any]) -> None:
    """
    追加一条成功经验。action_payload 建议含查询参数/SQL 等可序列化字段。
    任意失败静默返回，不抛到 ReAct 主循环。
    """
    if not experience_rag_enabled():
        return
    ui = (user_intent or "").strip()[:8000]
    tid = (executed_tool or "").strip()
    if not ui or not tid:
        return
    if not isinstance(action_payload, dict):
        return
    # 控制单行体积，避免 JSONL 爆炸
    safe_payload: dict[str, Any] = {}
    for _k, _v in list(action_payload.items())[:32]:
        sk = str(_k)[:120]
        if isinstance(_v, (str, int, float, bool)) or _v is None:
            safe_payload[sk] = _v
        elif isinstance(_v, dict):
            try:
                safe_payload[sk] = json.loads(json.dumps(_v, ensure_ascii=False)[:4000])
            except (TypeError, ValueError):
                safe_payload[sk] = str(_v)[:2000]
        else:
            safe_payload[sk] = str(_v)[:2000]
    path = _experience_path()
    record = {
        "user_intent": ui,
        "executed_tool": tid,
        "action_payload": safe_payload,
        "ts": time.time(),
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
            _trim_file_if_needed_unlocked(path)
    except OSError as e:
        logger.debug("[ExperienceRAG] 写入失败（静默跳过）: %s", e)
    except Exception as e:
        logger.debug("[ExperienceRAG] 写入异常（静默跳过）: %s", e)


def save_successful_action(user_intent: str, correct_action: dict[str, Any]) -> None:
    """兼容旧 API：correct_action = {tool_id, action_input}。"""
    if not isinstance(correct_action, dict):
        return
    tid = str(correct_action.get("tool_id") or "").strip()
    if not tid:
        return
    inp = correct_action.get("action_input")
    if isinstance(inp, dict):
        save_experience(user_intent, tid, inp)
    else:
        save_experience(user_intent, tid, {"action_input": str(inp or "")[:16000]})


def _trim_file_if_needed_unlocked(path: Path) -> None:
    cap = _max_file_lines()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    nonempty = [ln for ln in lines if ln.strip()]
    if len(nonempty) <= cap:
        return
    keep = nonempty[-cap:]
    try:
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError as e:
        logger.debug("[ExperienceRAG] 裁剪失败: %s", e)


def observation_suggests_sqlite_success(observation: str) -> bool:
    """启发式判断 SQLite 工具返回是否像成功（避免把明显错误写入经验库）。"""
    s = (observation or "").strip()
    if not s:
        return False
    low = s.lower()
    fail_markers = (
        "sql error",
        "sqlite error",
        "syntax error",
        "mcp error",
        "-32603",
        "-32602",
        "near ",
        "permission denied",
        "readonly",
        "not authorized",
    )
    if any(m in low for m in fail_markers):
        return False
    if '"ok": false' in low.replace(" ", "") or "'ok': false" in low:
        return False
    if s.startswith("{"):
        try:
            o = json.loads(s)
            if isinstance(o, dict) and o.get("ok") is False:
                return False
        except json.JSONDecodeError:
            pass
    return True


def tool_id_is_sqlite_read_or_write(tool_id: str) -> bool:
    """含 npm mcp-sqlite 的 query / read_records / update_records 等，与官方 read_query/write_query 同等对待。"""
    t = (tool_id or "").lower().replace("mcp:", "").strip()
    if t in (
        "read_query",
        "write_query",
        "query",
        "read_records",
        "update_records",
        "delete_records",
        "create_record",
    ):
        return True
    return "read_query" in t or "write_query" in t
