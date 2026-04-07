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
    return [sanitize_file_meta_dict(x) for x in items if isinstance(x, dict)]
