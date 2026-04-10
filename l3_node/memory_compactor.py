"""
L5 本地记忆「梦境合并」：l3_local.json 超阈值时由轻量 LLM 去重、消解冲突并原子覆写。

- 与 critic_agent 共用分类/轻量模型（JACHIN_CRITIC_MODEL 等）。
- 仅写入经 json.loads 校验的 JSON 数组（对象元素）；绝不写入 Markdown 污染。
- 失败 fail-open，不破坏主 ReAct 循环。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 与 local_memory._MAX_ENTRIES 对齐：合并后再截断，避免无限增长
_MAX_FINAL_ENTRIES = 200

_compact_lock = asyncio.Lock()

_MAX_LLM_INPUT_CHARS = 52_000
_MAX_OUTPUT_ENTRIES = 120
_COMPACT_TIMEOUT_SEC = 120.0
# 与 LLM 约定：若启用 response_format=json_object，必须用对象包裹数组
_MEMORY_COMPACT_JSON_KEY = "_memory_compact_items"

# 从杂乱输出中提取首个完整 JSON 数组片段（括号配对，尊重字符串内引号）
_JSON_ARRAY_HEAD_RE = re.compile(r"\[")


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    s = re.sub(r"^```\w*\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s).strip()
    return s


def _extract_balanced_json_array(text: str) -> str | None:
    """从 text 中截取从第一个 '[' 起括号平衡的 JSON 数组子串；失败则 None。"""
    m = _JSON_ARRAY_HEAD_RE.search(text)
    if not m:
        return None
    start = m.start()
    depth = 0
    in_str = False
    esc = False
    quote = ""
    s = text
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            continue
        if c in "\"'":
            in_str = True
            quote = c
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _coerce_memory_entry_list(obj: Any) -> list[dict[str, Any]] | None:
    """只接受对象列表；过滤非 dict。"""
    if not isinstance(obj, list):
        return None
    out: list[dict[str, Any]] = []
    for x in obj:
        if isinstance(x, dict):
            out.append(x)
    return out if out else None


def _parse_llm_memory_json(content: str) -> list[dict[str, Any]] | None:
    """
    强韧解析：禁止把未校验文本写入磁盘。
    顺序：去围栏 → 整段 json.loads → 对象键 _memory_compact_items → 括号平衡数组 → 宽松 re 兜底。
    """
    raw = _strip_json_fence((content or "").strip())
    if not raw:
        return None

    # 1) 整段即 JSON
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and _MEMORY_COMPACT_JSON_KEY in obj:
            return _coerce_memory_entry_list(obj.get(_MEMORY_COMPACT_JSON_KEY))
        if isinstance(obj, list):
            return _coerce_memory_entry_list(obj)
    except json.JSONDecodeError:
        pass

    # 2) 平衡括号数组
    frag = _extract_balanced_json_array(raw)
    if frag:
        try:
            obj = json.loads(frag)
            return _coerce_memory_entry_list(obj)
        except json.JSONDecodeError:
            pass

    # 3) 最后兜底：DOTALL 贪婪匹配首个 [...]（可能在 Markdown 噪声中）
    m = re.search(r"\[[\s\S]*\]", raw, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return _coerce_memory_entry_list(obj)
        except json.JSONDecodeError:
            pass

    return None


def _validate_roundtrip(entries: list[dict[str, Any]]) -> bool:
    try:
        json.dumps(entries, ensure_ascii=False)
        return True
    except (TypeError, ValueError):
        return False


def _atomic_write_json_array(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".compact.tmp")
    payload = json.dumps(entries, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _shadow_path(main: Path) -> Path:
    return main.parent / f"{main.name}.shadow"


def _unlink_quiet(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError as e:
        logger.debug("[MemoryCompact] 删除影子文件跳过: %s", e)


def _entry_fingerprint(e: dict[str, Any]) -> tuple[str, str]:
    return (
        str(e.get("tag") or "").strip(),
        (str(e.get("content") or "").strip())[:400],
    )


def _merge_post_snapshot_entries(
    merged: list[dict[str, Any]],
    live_main: list[dict[str, Any]],
    snapshot_ts: float,
) -> list[dict[str, Any]]:
    """
    将快照之后在主库上新增的条目并入 LLM 合并结果，避免并发聊天写入被覆盖。
    """
    fp = {_entry_fingerprint(x) for x in merged if isinstance(x, dict)}
    out: list[dict[str, Any]] = list(merged)
    for e in live_main:
        if not isinstance(e, dict):
            continue
        try:
            ts = float(e.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= snapshot_ts:
            continue
        k = _entry_fingerprint(e)
        if k in fp:
            continue
        out.append(e)
        fp.add(k)
    out.sort(key=lambda x: float(x.get("timestamp") or 0), reverse=True)
    if len(out) > _MAX_FINAL_ENTRIES:
        out = out[:_MAX_FINAL_ENTRIES]
    return out


async def compact_local_memory_if_needed(
    file_path: str,
    threshold: int = 150,
    *,
    force: bool = False,
) -> str:
    """
    若 JSON 数组条目数 > threshold（或 force=True 为显式口令「立刻整理」），调用轻量 LLM 合并后原子覆写。

    Args:
        threshold: 自动/定时/每轮检查路径下的条数下限；force=True 时忽略。
        force: 用户显式口令（整理本地记忆/梦境合并等）时为 True，**无视阈值**立即尝试合并；
            主库为空数组时仍不调用 LLM，返回简短说明。

    Returns:
        成功：简短中文报告；未触发/失败：空字符串（fail-open，不抛错）。
    """
    v = (os.environ.get("JACHIN_MEMORY_COMPACT_ENABLED") or "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return ""

    async with _compact_lock:
        try:
            from l3_node.memory_compact_control import (
                is_memory_compact_cancel_requested,
                reset_memory_compact_cancel,
            )

            reset_memory_compact_cancel()
        except ImportError:
            def is_memory_compact_cancel_requested() -> bool:
                return False

            reset_memory_compact_cancel = lambda: None  # noqa: E731

        path = Path(file_path).expanduser().resolve()
        shadow = _shadow_path(path)
        _unlink_quiet(shadow)

        if not path.exists():
            return ""

        try:
            raw_text = path.read_text(encoding="utf-8")
            entries = json.loads(raw_text)
        except Exception as e:
            logger.debug("[MemoryCompact] 读取/解析跳过: %s", e)
            return ""

        if not isinstance(entries, list):
            return ""

        n_before = len(entries)
        if n_before == 0:
            if force:
                return "本地记忆暂无条目，无需合并。"
            return ""

        if not force and n_before <= threshold:
            return ""

        snapshot_ts = time.time()
        try:
            shutil.copy2(path, shadow)
        except OSError as e:
            logger.warning("[MemoryCompact] 无法建立影子副本，降级为直接读主库: %s", e)
            snapshot_ts = time.time()

        if is_memory_compact_cancel_requested():
            _unlink_quiet(shadow)
            return ""

        try:
            from l3_node.critic_agent import critic_model_litellm_id

            model = critic_model_litellm_id()
        except Exception:
            model = "dashscope/qwen-turbo"

        try:
            work_text = shadow.read_text(encoding="utf-8") if shadow.exists() else raw_text
            work_entries = json.loads(work_text)
            if not isinstance(work_entries, list):
                work_entries = entries
        except Exception:
            work_entries = entries

        dump = json.dumps(work_entries, ensure_ascii=False)
        if len(dump) > _MAX_LLM_INPUT_CHARS:
            dump = dump[:_MAX_LLM_INPUT_CHARS] + "\n…(truncated for LLM)"

        system = (
            "你是 Jachin AI OS 的本地记忆治理模块。\n"
            "**只输出合法 JSON，禁止 Markdown（禁止 ```、禁止 # 标题、禁止任何解释性文字前后缀）。**\n"
            "输出必须是 **单个 JSON 对象**，且 **仅含一个键** `"
            + _MEMORY_COMPACT_JSON_KEY
            + "`，值为对象数组；元素尽量保留 tag、content、source、timestamp 等键；"
            "合并重复与冲突事实（新版本覆盖旧版本），删除废话与冗余。\n"
            "示例（结构示意，勿照抄内容）："
            '{"'
            + _MEMORY_COMPACT_JSON_KEY
            + '":[{"tag":"fact","content":"…"}]}'
        )
        user = (
            "以下是一份冗长的系统本地记忆 JSON 数组。请输出合并后的 `"
            + _MEMORY_COMPACT_JSON_KEY
            + "` 数组（包装在上述单键对象内）。\n"
            "禁止输出数组以外的 Markdown 或自然语言。\n\n"
            f"输入（JSON 数组）：\n{dump}"
        )

        try:
            import litellm
        except ImportError:
            logger.debug("[MemoryCompact] litellm 未安装，跳过")
            _unlink_quiet(shadow)
            return ""

        try:
            from l3_node.llm_client import _effective_max_tokens_for_model

            max_t = _effective_max_tokens_for_model(model, 4096)
        except Exception:
            max_t = 4096

        try:
            timeout = float(os.environ.get("JACHIN_MEMORY_COMPACT_TIMEOUT_SEC") or str(_COMPACT_TIMEOUT_SEC))
        except (TypeError, ValueError):
            timeout = _COMPACT_TIMEOUT_SEC

        use_json_object = str(os.environ.get("JACHIN_MEMORY_COMPACT_RESPONSE_JSON", "1")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        async def _do_call(extra: dict[str, Any] | None) -> Any:
            kw: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_tokens": max_t,
                "timeout": timeout,
                "stream": False,
            }
            if extra:
                kw.update(extra)
            return await litellm.acompletion(**kw)

        if is_memory_compact_cancel_requested():
            _unlink_quiet(shadow)
            return ""

        resp = None
        try:
            if use_json_object:
                try:
                    resp = await _do_call({"response_format": {"type": "json_object"}})
                except Exception as e1:
                    logger.debug("[MemoryCompact] response_format 不可用，重试无该参数: %s", e1)
                    resp = await _do_call(None)
            else:
                resp = await _do_call(None)
        except Exception as e:
            logger.warning("[MemoryCompact] LLM 调用失败，fail-open: %s", e)
            _unlink_quiet(shadow)
            return ""

        if is_memory_compact_cancel_requested():
            _unlink_quiet(shadow)
            return ""

        try:
            choice0 = resp.choices[0] if resp and getattr(resp, "choices", None) else None
            msg = getattr(choice0, "message", None) if choice0 else None
            content = (getattr(msg, "content", None) or "") if msg else ""
            if isinstance(content, list):
                content = "".join(
                    str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content
                )
        except Exception as e:
            logger.warning("[MemoryCompact] 读取响应失败: %s", e)
            _unlink_quiet(shadow)
            return ""

        merged = _parse_llm_memory_json(str(content))
        if not merged:
            logger.warning("[MemoryCompact] 无法解析为合法 JSON 对象列表，不覆写")
            _unlink_quiet(shadow)
            return ""

        if len(merged) > _MAX_OUTPUT_ENTRIES:
            merged = merged[:_MAX_OUTPUT_ENTRIES]

        if not _validate_roundtrip(merged):
            logger.warning("[MemoryCompact] 结果无法 json 序列化，不覆写")
            _unlink_quiet(shadow)
            return ""

        live_main: list[dict[str, Any]] = []
        try:
            live_raw = path.read_text(encoding="utf-8")
            live_obj = json.loads(live_raw)
            if isinstance(live_obj, list):
                live_main = [x for x in live_obj if isinstance(x, dict)]
        except Exception as e:
            logger.debug("[MemoryCompact] 重读主库合并尾部跳过: %s", e)

        merged = _merge_post_snapshot_entries(merged, live_main, snapshot_ts)

        if is_memory_compact_cancel_requested():
            _unlink_quiet(shadow)
            return ""

        try:
            _atomic_write_json_array(path, merged)
        except Exception as e:
            logger.warning("[MemoryCompact] 原子写入失败: %s", e)
            _unlink_quiet(shadow)
            return ""

        _unlink_quiet(shadow)

        n_after = len(merged)
        logger.info("[MemoryCompact] 已合并写入（双缓冲+尾部合并）%s 条 → %s 条", n_before, n_after)
        try:
            from l3_node.memory_compact_schedule import record_compact_completed

            record_compact_completed()
        except ImportError:
            pass
        return f"本地记忆坍缩完成。原条目数: {n_before}，压缩后条目数: {n_after}。请向统帅汇报已完成。"
