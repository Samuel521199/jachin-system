#!/usr/bin/env python3
"""
修复 ~/.jachin/mcp_servers.json 中过期的 hr-atomic-tools 路径（换目录/仓库名后常见）。

用法（仓库根）:
  python scripts/repair_mcp_servers.py
  python scripts/repair_mcp_servers.py --project-root D:\\path\\to\\jachin-system-main
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="jachin-system 仓库根（默认：本脚本的上两级目录）",
    )
    args = ap.parse_args()
    script = Path(__file__).resolve()
    root = (args.project_root or script.parent.parent).resolve()
    hr_server = root / "skills_repo" / "plugin" / "com.jachin.hr.recruitment" / "server.py"
    if not hr_server.is_file():
        print(f"[repair_mcp_servers] 未找到 HR 入口: {hr_server}", file=sys.stderr)
        return 1

    cfg_path = Path.home() / ".jachin" / "mcp_servers.json"
    if not cfg_path.is_file():
        print(f"[repair_mcp_servers] 无配置文件，跳过: {cfg_path}")
        return 0

    raw = cfg_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[repair_mcp_servers] JSON 解析失败: {e}", file=sys.stderr)
        return 1

    import shutil

    py_exe = shutil.which("python") or shutil.which("python3")
    if not py_exe:
        print("[repair_mcp_servers] 未找到 python 可执行文件", file=sys.stderr)
        return 1

    changed = False
    if isinstance(data, dict) and "mcp_servers" in data and isinstance(data["mcp_servers"], list):
        for entry in data["mcp_servers"]:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id") or "") != "hr-atomic-tools":
                continue
            entry["command"] = py_exe
            entry["args"] = [str(hr_server.resolve())]
            changed = True
            print(f"[repair_mcp_servers] 已更新 hr-atomic-tools -> {hr_server}")
    elif isinstance(data, dict) and "mcpServers" in data and isinstance(data["mcpServers"], dict):
        inner = data["mcpServers"].get("hr-atomic-tools")
        if isinstance(inner, dict):
            inner["command"] = py_exe
            inner["args"] = [str(hr_server.resolve())]
            changed = True
            print(f"[repair_mcp_servers] 已更新 mcpServers.hr-atomic-tools -> {hr_server}")

    if not changed:
        print(
            "[repair_mcp_servers] 未找到 hr-atomic-tools；"
            "可运行: cd skills_repo/plugin && python install.py --jachin <仓库根>"
        )
        return 0

    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[repair_mcp_servers] 已写入 {cfg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
