#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从仓库根 .env 提取 Lark/K11 飞书相关键，生成 ``l3_node/packaged_lark_env_generated.py``，
供侧车 PyInstaller 单文件exe内嵌、免目标机再配 .env。

用法（仓库根）:
  python scripts/emit_packaged_lark_env.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 与 k11 / resolve_lark 读取顺序对齐；仅嵌入这些键（打侧车前须保证根 .env 为分发目标机器人）
_KEYS = (
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "LARK_CHAT_ID",
    "LARK_USE_FEISHU",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "K11_SMOKE_LARK_APP_ID",
    "K11_SMOKE_LARK_APP_SECRET",
    "K11_SMOKE_LARK_WIKI_URL",
    "K11_SMOKE_LARK_TABLE_ID",
    "K11_SMOKE_LARK_SHEET_ID",
    "K11_SMOKE_LARK_NOTIFY_CHAT_ID",
)
_KEYSET = frozenset(_KEYS)


def _parse_dotenv_subset(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        k, _, rest = s.partition("=")
        k = k.strip()
        if k not in _KEYSET:
            continue
        v = rest.strip()
        if not v:
            out[k] = ""
            continue
        if (v[0] == v[-1]) and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def emit(project_root: Path) -> int:
    root = project_root.resolve()
    env_p = root / ".env"
    data = _parse_dotenv_subset(env_p)
    out = root / "l3_node" / "packaged_lark_env_generated.py"
    lines = [
        "# -*- coding: utf-8 -*-",
        "# 由 scripts/emit_packaged_lark_env.py 根据仓库根 .env 生成，勿手改。",
        "PACKAGED_LARK_ENV: dict[str, str] = {",
    ]
    for key in _KEYS:
        if key not in data:
            continue
        val = data[key]
        lines.append(f"    {key!r}: {val!r},")
    lines.append("}")
    body = "\n".join(lines) + "\n"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"[emit_packaged_lark_env] 写入失败: {e}", file=sys.stderr)
        return 1
    n = len([k for k in _KEYS if k in data and data.get(k, "").strip()])
    print(
        f"[emit_packaged_lark_env] 已写入 {out.relative_to(root)}，"
        f"自 .env 取得非空键 {n} 个（共扫描键 {len(_KEYS)}）"
    )
    if not env_p.is_file():
        print("  提示: 未找到 .env，生成为空表；可编辑 .env 后重跑本脚本或打侧车。")
    return 0


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    return emit(root)


if __name__ == "__main__":
    raise SystemExit(main())
