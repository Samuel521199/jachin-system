"""
多模态调试：日志中摘要 user.content / messages，避免打印整段 base64。

完整结构（脱敏）：
- 默认关闭逐条详细：`JACHIN_L3_LOG_LITELLM_DETAIL=1` 时 `format_litellm_messages_detailed` 逐条说明 role / content 形态。
- `JACHIN_L3_LOG_LITELLM_JSON=1`：额外输出与 LiteLLM 请求同构的 JSON（字符串已脱敏），体积大，仅排障开。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any


def _mask_data_url(u: str) -> str:
    s = (u or "").strip()
    low = s.lower()
    if low.startswith("data:image/") and ";base64" in low and "," in s:
        head = s.split(",", 1)[0]
        return f"{head},<base64 {len(s)} chars>"
    if len(s) > 160:
        return s[:80] + f"…({len(s)} chars)"
    return s


def summarize_openai_user_content_for_log(content: Any, *, max_text_preview: int = 160) -> str:
    """单条 user.content：类型、是否含 image_url、文本预览（截断）。"""
    if isinstance(content, str):
        t = content.replace("\n", " ").strip()
        prev = t[:max_text_preview] + ("…" if len(t) > max_text_preview else "")
        return f"type=str chars={len(content)} preview={prev!r}"
    if isinstance(content, list):
        n_txt = n_img = n_other = 0
        text_prev = ""
        img_hints: list[str] = []
        for p in content:
            if not isinstance(p, dict):
                n_other += 1
                continue
            pt = str(p.get("type") or "")
            if pt == "text" or (pt == "" and "text" in p and "image" not in p):
                n_txt += 1
                tx = str(p.get("text") or "")
                if not text_prev and tx.strip():
                    tp = tx.replace("\n", " ").strip()
                    text_prev = tp[:max_text_preview] + ("…" if len(tp) > max_text_preview else "")
            elif pt == "image_url":
                n_img += 1
                iu = p.get("image_url")
                u = ""
                if isinstance(iu, dict):
                    u = str(iu.get("url") or "")
                elif isinstance(iu, str):
                    u = iu
                img_hints.append(_mask_data_url(u)[:120])
            elif pt == "" and "image" in p:
                n_img += 1
                img_hints.append(_mask_data_url(str(p.get("image") or ""))[:120])
            else:
                n_other += 1
        return (
            f"type=list parts={len(content)} text_blocks={n_txt} image_blocks={n_img} other={n_other} "
            f"text_preview={text_prev!r} image_hints={img_hints}"
        )
    return f"type={type(content).__name__} repr={repr(content)[:120]}"


def summarize_messages_for_litellm_dispatch(
    messages: list[dict[str, Any]] | None,
    *,
    purpose: str = "",
    max_msgs: int = 24,
) -> str:
    """发往 LiteLLM 的 messages 尾部摘要：逐条 role + content 摘要。"""
    if not messages:
        return "messages=<empty>"
    ms = messages[-max_msgs:]
    lines: list[str] = [f"purpose={purpose} total={len(messages)} tail={len(ms)}"]
    for i, m in enumerate(ms):
        role = (m.get("role") or "?").strip()
        c = m.get("content")
        summ = summarize_openai_user_content_for_log(c, max_text_preview=120)
        lines.append(f"  [{i}] role={role} {summ}")
    return "\n".join(lines)


def summarize_attachments_ingress(
    raw_att: list[dict[str, Any]] | None,
    *,
    run_id: str = "",
) -> str:
    """进入 build_openai_user_content 前的附件列表（仅元信息）。"""
    if not raw_att:
        return f"run_id={run_id[:12]} n_attachments=0"
    bits: list[str] = []
    for j, a in enumerate(raw_att[:8]):
        if not isinstance(a, dict):
            bits.append(f"[{j}]<non-dict>")
            continue
        name = str(a.get("name") or a.get("filename") or "")[:64]
        mime = str(a.get("mime") or a.get("content_type") or "")[:32]
        hi = bool(a.get("has_image") or a.get("is_image"))
        sz = a.get("size_bytes") or a.get("size") or 0
        iu = a.get("image_url")
        url_hint = ""
        if isinstance(iu, dict):
            u = str(iu.get("url") or "")
            url_hint = _mask_data_url(u)[:100]
        elif isinstance(iu, str):
            url_hint = _mask_data_url(iu)[:100]
        bits.append(
            f"[{j}] name={name!r} mime={mime!r} has_image={hi} size_bytes={sz} url={url_hint!r}"
        )
    extra = f" (+{len(raw_att) - 8} more)" if len(raw_att) > 8 else ""
    return f"run_id={run_id[:12]} n_attachments={len(raw_att)}{extra}\n  " + "\n  ".join(bits)


def _mask_long_string(s: str, *, head: int = 240) -> str:
    if not isinstance(s, str):
        return str(s)
    t = s.strip()
    low = t.lower()
    if low.startswith("data:image/") and ";base64" in low and "," in t:
        pre = t.split(",", 1)[0]
        rest = t.split(",", 1)[1]
        return f"{pre},<base64 len={len(rest)} chars>"
    if len(t) > head:
        return t[:head] + f"…<truncated total_chars={len(t)}>"
    return t


def sanitize_message_content_for_json_log(content: Any, *, system_max_preview: int = 600) -> Any:
    """深拷贝式构造可 JSON 序列化的 content，脱敏长文本与 data URL。"""
    if content is None:
        return None
    if isinstance(content, str):
        if len(content) > system_max_preview * 8:
            return (
                f"<str len={len(content)} preview={_mask_long_string(content[:system_max_preview], head=system_max_preview)!r} …>"
            )
        return _mask_long_string(content, head=system_max_preview)
    if isinstance(content, list):
        out: list[Any] = []
        for p in content:
            if not isinstance(p, dict):
                out.append(p)
                continue
            d: dict[str, Any] = {}
            for k, v in p.items():
                if k == "text" and isinstance(v, str):
                    d[k] = _mask_long_string(v, head=4000)
                elif k == "image" and isinstance(v, str):
                    d[k] = _mask_long_string(v, head=120)
                elif k == "image_url":
                    if isinstance(v, dict) and isinstance(v.get("url"), str):
                        d[k] = {"url": _mask_long_string(v["url"], head=120)}
                    elif isinstance(v, str):
                        d[k] = _mask_long_string(v, head=120)
                    else:
                        d[k] = v
                else:
                    d[k] = v
            out.append(d)
        return out
    return content


def sanitize_messages_for_json_dump(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """供「全量结构」日志：与发往 litellm 的 messages 同形，仅脱敏。"""
    if not messages:
        return []
    out: list[dict[str, Any]] = []
    for m in messages:
        mm: dict[str, Any] = {"role": m.get("role")}
        if "content" in m:
            mm["content"] = sanitize_message_content_for_json_log(m.get("content"))
        if m.get("name"):
            mm["name"] = m.get("name")
        if m.get("tool_calls") is not None:
            mm["tool_calls"] = "<omitted>" if m.get("tool_calls") else None
        out.append(mm)
    return out


def format_litellm_messages_detailed(
    messages: list[dict[str, Any]] | None,
    *,
    purpose: str = "",
    model: str = "",
    max_msgs: int = 64,
) -> str:
    """
    人类可读：每条 message 的 content 是 str 还是 list、每块的键（OpenAI vs DashScope 原生）。
    """
    if not messages:
        return f"purpose={purpose} model={model} messages=<empty>"
    ms = messages[-max_msgs:]
    lines: list[str] = [
        f"[outbound_detail] purpose={purpose} model={model} total_msgs={len(messages)} showing_tail={len(ms)}"
    ]
    for idx, m in enumerate(ms):
        global_idx = len(messages) - len(ms) + idx
        role = (m.get("role") or "?").strip()
        c = m.get("content")
        if isinstance(c, str):
            lines.append(
                f"  msg[{global_idx}] role={role} content_type=str chars={len(c)} "
                f"preview={_mask_long_string(c[:800], head=400)!r}"
            )
        elif isinstance(c, list):
            lines.append(f"  msg[{global_idx}] role={role} content_type=list n_parts={len(c)}")
            for j, p in enumerate(c):
                if not isinstance(p, dict):
                    lines.append(f"    part[{j}] <non-dict> {type(p).__name__}")
                    continue
                keys = sorted(p.keys())
                if p.get("type") == "text":
                    lines.append(
                        f"    part[{j}] OpenAI {{type:text}} text_len={len(str(p.get('text') or ''))} "
                        f"preview={_mask_long_string(str(p.get('text') or '')[:200], head=120)!r}"
                    )
                elif p.get("type") == "image_url":
                    iu = p.get("image_url")
                    u = ""
                    if isinstance(iu, dict):
                        u = str(iu.get("url") or "")
                    elif isinstance(iu, str):
                        u = iu
                    lines.append(
                        f"    part[{j}] OpenAI {{type:image_url}} url={_mask_data_url(u)}"
                    )
                elif "text" in p and "type" not in p:
                    lines.append(
                        f"    part[{j}] DashScope_native {{text}} len={len(str(p.get('text') or ''))} "
                        f"preview={_mask_long_string(str(p.get('text') or '')[:200], head=120)!r}"
                    )
                elif "image" in p and "type" not in p:
                    lines.append(
                        f"    part[{j}] DashScope_native {{image}} url={_mask_data_url(str(p.get('image') or ''))}"
                    )
                else:
                    lines.append(f"    part[{j}] keys={keys} (其它形态)")
        else:
            lines.append(f"  msg[{global_idx}] role={role} content_type={type(c).__name__}")
    return "\n".join(lines)


def log_litellm_outbound_messages(
    log: logging.Logger,
    messages: list[dict[str, Any]] | None,
    *,
    purpose: str,
    model: str,
    stream: bool,
) -> None:
    """
    关键路径：打印「即将发给 LiteLLM」的消息结构（脱敏）。

    - 默认 INFO：逐条详细形态（format_litellm_messages_detailed）。
    - JACHIN_L3_LOG_LITELLM_DETAIL=0：跳过详细块，仅保留原有 dispatch 摘要调用方自行处理。
    - JACHIN_L3_LOG_LITELLM_JSON=1：额外打一条 JSON（整段 messages 脱敏，可能仍较大）。
    """
    if not messages:
        return
    if os.environ.get("JACHIN_L3_LOG_LITELLM_DETAIL", "0").strip().lower() in ("0", "false", "no"):
        return
    tag = "[L3 LLM][outbound_detail]"
    if stream:
        tag = "[L3 LLM][outbound_detail][stream]"
    try:
        body = format_litellm_messages_detailed(
            list(messages), purpose=str(purpose), model=str(model)
        )
        log.info("%s\n%s", tag, body)
    except Exception as e:
        log.debug("%s 格式化失败: %s", tag, e)

    if os.environ.get("JACHIN_L3_LOG_LITELLM_JSON", "").strip().lower() in ("1", "true", "yes"):
        try:
            safe = sanitize_messages_for_json_dump(list(messages))
            blob = json.dumps(safe, ensure_ascii=False, indent=2)
            max_json = 120_000
            total_len = len(blob)
            if total_len > max_json:
                blob = blob[:max_json] + f"\n…<json truncated total_chars={total_len}>"
            log.info(
                "%s[json] purpose=%s model=%s\n%s",
                tag,
                purpose,
                model,
                blob,
            )
        except Exception as e:
            log.debug("%s json 序列化失败: %s", tag, e)
