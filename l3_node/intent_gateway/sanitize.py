"""
§12.1 附件元数据无害化：防文件名等注入 L2 提示拼接。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# 保守白名单：字母数字、空格、点、下划线、中划线、括号、中文常见标点
_SAFE_NAME_RE = re.compile(r"^[\w\s.\-–—()（）\[\]【】、，。;；:：'\"«»]+$", re.UNICODE)
_MAX_NAME_LEN = 256
# 与桌面 Omni 一致：单次最多附件数、声明体积上限（实体仍以解码后长度为准）
_MAX_ATTACHMENT_ITEMS = 5
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


def _decoded_base64_payload_len_approx(b64: str) -> int:
    """不含 padding 的保守近似：len*3/4。"""
    s = re.sub(r"\s+", "", b64 or "")
    if not s:
        return 0
    n = len(s)
    pad = 0
    if s.endswith("=="):
        pad = 2
    elif s.endswith("="):
        pad = 1
    return max((n * 3) // 4 - pad, 1)


def _effective_attachment_size_for_trim(x: dict[str, Any]) -> int:
    """
    门限用「有效字节」：WebSocket 内联图常把 size_bytes 写成 len(data URL 整串)，
    比解码后体积约大 4/3，易误超 5MB 被整条丢弃 → 多模态链路无 image_url。
    data:image/*;base64 时优先用 base64 段解码估值参与比较。
    """
    try:
        sz = int(x.get("size_bytes") or x.get("size") or 0)
    except (TypeError, ValueError):
        sz = 0
    url = ""
    iu = x.get("image_url")
    if isinstance(iu, dict):
        url = str(iu.get("url") or "")
    elif isinstance(iu, str):
        url = iu
    if not url and isinstance(x.get("url"), str):
        url = str(x.get("url"))
    ul = url.strip().lower()
    if ul.startswith("data:image/") and ";base64" in ul:
        try:
            comma = url.index(",")
            dec_est = _decoded_base64_payload_len_approx(url[comma + 1 :])
            if dec_est > 0:
                return dec_est
        except ValueError:
            pass
    return sz


def trim_attachments_metadata_list(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """截断到最多 N 条，跳过声明体积过大的项。"""
    if not items:
        return []
    out: list[dict[str, Any]] = []
    for x in items:
        if len(out) >= _MAX_ATTACHMENT_ITEMS:
            break
        if not isinstance(x, dict):
            continue
        sz = _effective_attachment_size_for_trim(x)
        if sz > _MAX_ATTACHMENT_BYTES:
            continue
        out.append(x)
    return out


def sanitize_display_name(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s:
        return "unnamed"
    if len(s) > _MAX_NAME_LEN:
        s = s[:_MAX_NAME_LEN]
    if not _SAFE_NAME_RE.match(s):
        # 剥离控制字符与可疑片段，仅保留 alnum 与少量符号
        cleaned = "".join(
            c
            for c in s
            if c.isalnum() or c in " ._-–—()（）[]【】、，。;；:："
        ).strip()
        return cleaned[:_MAX_NAME_LEN] or "sanitized_file"
    return s


def fingerprint_name(raw: str | None) -> str:
    """日志/槽位用短指纹，不落原文。"""
    h = hashlib.sha256((raw or "").encode("utf-8", errors="replace")).hexdigest()
    return h[:16]


@dataclass
class SanitizedFileMeta:
    """供 Feature Slot 使用；name 已清洗。"""

    size_bytes: int = 0
    mime: str = ""
    name_safe: str = ""
    name_fingerprint: str = ""
    has_image: bool = False
    raw_keys: dict[str, Any] = field(default_factory=dict)


def sanitize_file_meta_dict(d: dict[str, Any]) -> SanitizedFileMeta:
    name_raw = str(d.get("name") or d.get("filename") or "")
    mime = str(d.get("mime") or d.get("content_type") or "")[:128]
    try:
        sz = int(d.get("size_bytes") or d.get("size") or 0)
    except (TypeError, ValueError):
        sz = 0
    hi = bool(d.get("has_image") or d.get("is_image"))
    safe = sanitize_display_name(name_raw)
    return SanitizedFileMeta(
        size_bytes=max(0, sz),
        mime=mime,
        name_safe=safe,
        name_fingerprint=fingerprint_name(name_raw),
        has_image=hi,
        raw_keys={k: d.get(k) for k in ("checksum", "storage_tier", "uri") if k in d},
    )


def sanitize_attachments_list(items: list[dict[str, Any]] | None) -> list[SanitizedFileMeta]:
    if not items:
        return []
    trimmed = trim_attachments_metadata_list(items)
    return [sanitize_file_meta_dict(x) for x in trimmed if isinstance(x, dict)]
