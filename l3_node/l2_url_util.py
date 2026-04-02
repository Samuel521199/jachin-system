"""L2 网关基址规范化（环境变量 / 配置误填时避免 http://http://… 等无效 URL）。"""
from __future__ import annotations

import re


def normalize_l2_base_url(raw: str | None, default: str = "http://localhost:18888") -> str:
    s = (raw or "").strip()
    if not s:
        return default
    s = s.rstrip("/")
    low = s.lower()
    while low.startswith("http://http://") or low.startswith("http://https://"):
        s = s[7:]
        low = s.lower()
    while low.startswith("https://https://") or low.startswith("https://http://"):
        s = s[8:]
        low = s.lower()
    # 误把端口写进 path：http://host/:18888 → http://host:18888
    s = re.sub(r"/:(\d+)$", r":\1", s)
    if "://" not in s:
        s = "http://" + s.lstrip("/")
    return s.rstrip("/")
