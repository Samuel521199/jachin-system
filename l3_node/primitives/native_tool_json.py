"""
Native 工具 Action Input 解析：模型/API function calling 常输出 JSON，
json.loads 失败时须 regex 回退，避免整段 JSON 被当作 file_path 拼进 workspace。
"""
from __future__ import annotations

import json
import re
from typing import Any


_FILE_PATH_KEY_RE = re.compile(
    r'"(?:file_path|path)"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)


def _unescape_json_string(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except json.JSONDecodeError:
        return s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def coerce_file_path_from_tool_input(inp: str) -> str:
    """从 Action Input 提取 file_path；失败时返回去引号的原文（非 JSON 裸路径）。"""
    raw = (inp or "").strip()
    if not raw:
        return ""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1].strip()
    if raw.startswith("{") or '"file_path"' in raw or '"path"' in raw:
        try:
            o = json.loads(raw)
            if isinstance(o, dict):
                fp = str(o.get("file_path") or o.get("path") or "").strip()
                if fp:
                    return fp
        except json.JSONDecodeError:
            m = _FILE_PATH_KEY_RE.search(raw)
            if m:
                return _unescape_json_string(m.group(1)).strip()
            # 截断 JSON：仍可能有 "file_path":"..." 片段
        if raw.startswith("{"):
            # 勿把整段 JSON 当路径
            return ""
    return raw


def parse_fs_write_tool_input(inp: str) -> dict[str, str]:
    """解析 fs_write Action Input → file_path + content。"""
    raw = (inp or "").strip()
    out: dict[str, str] = {"file_path": "", "content": ""}
    if not raw:
        return out
    if raw.startswith("{"):
        try:
            o = json.loads(raw)
            if isinstance(o, dict):
                out["file_path"] = str(o.get("file_path") or o.get("path") or "").strip()
                ct = o.get("content")
                out["content"] = "" if ct is None else str(ct)
                return out
        except json.JSONDecodeError:
            m = _FILE_PATH_KEY_RE.search(raw)
            if m:
                out["file_path"] = _unescape_json_string(m.group(1)).strip()
            # content 字段：从 ,"content": 到末尾或下一键（大 JSON 可能被截断）
            cm = re.search(r'"content"\s*:\s*"(.*)', raw, re.DOTALL)
            if cm:
                body = cm.group(1)
                # 去掉尾部截断的 "} 等
                body = re.sub(r'"\s*}\s*$', "", body.rstrip())
                out["content"] = _unescape_json_string(body)
            if out["file_path"] or out["content"]:
                return out
    if "," in raw and "=" in raw:
        for part in raw.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    lines = raw.split("\n")
    out["file_path"] = lines[0].strip() if lines else ""
    out["content"] = "\n".join(lines[1:]) if len(lines) > 1 else ""
    return out


def coerce_tool_input_dict(inp: str) -> dict[str, Any] | None:
    """尽力 json.loads；失败返回 None。"""
    raw = (inp or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        o = json.loads(raw)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        return None
