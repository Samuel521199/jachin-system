#!/usr/bin/env python3
"""
诊断 Tavily API Key 是否进入「父进程 os.environ」以及 resolve 后的 stdio env（脱敏）。

用法（仓库根）::
  python scripts/diagnose_tavily_env.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 仓库根
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    os.chdir(_ROOT)
    print("[1] cwd =", os.getcwd())
    print("[2] JACHIN_APP_ROOT =", os.environ.get("JACHIN_APP_ROOT", "(未设置)"))

    from core.l3_dotenv_merge import merge_l3_dotenv_into_os
    from core.mcp_embedded_runtime import mask_secret_for_log, resolve_mcp_cfg_placeholders

    merge_l3_dotenv_into_os()
    par = (os.environ.get("TAVILY_API_KEY") or "").strip()
    print("[3] 合并后 os.environ TAVILY_API_KEY =", mask_secret_for_log(par))

    sample = {
        "id": "tavily-search",
        "command": "npx",
        "args": ["-y", "tavily-mcp@latest"],
        "env": {"TAVILY_API_KEY": "${TAVILY_API_KEY}"},
    }
    out = resolve_mcp_cfg_placeholders(sample)
    env = out.get("env") or {}
    sto = str(env.get("TAVILY_API_KEY") or "").strip()
    print("[4] resolve 后 stdio env TAVILY =", mask_secret_for_log(sto))
    print("[5] 子进程将收到非空 Key:", bool(sto))

    cfg_path = Path.home() / ".jachin" / "mcp_servers.json"
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            servers = raw.get("mcp_servers", raw) if isinstance(raw, dict) else raw
            for s in servers or []:
                if not isinstance(s, dict):
                    continue
                sid = str(s.get("id") or "").lower()
                args = s.get("args") or []
                if "tavily" not in sid and not any("tavily" in str(a).lower() for a in args):
                    continue
                ro = resolve_mcp_cfg_placeholders(dict(s))
                e = ro.get("env") or {}
                tv = str(e.get("TAVILY_API_KEY") or "").strip()
                print(f"[6] 用户配置 {s.get('id')!r} → stdio TAVILY =", mask_secret_for_log(tv))
        except Exception as e:
            print("[6] 读取 ~/.jachin/mcp_servers.json 失败:", e)
    else:
        print("[6] 无 ~/.jachin/mcp_servers.json（跳过）")

    if not par:
        print("\n结论: 父进程仍无 TAVILY_API_KEY。请检查项目根 .env 与 ~/.jachin/.env，并确认已保存。")
        return 1
    if not sto:
        print("\n结论: 父进程有 Key 但 resolve 后仍空（不应发生），请报 issue。")
        return 2
    print("\n结论: 环境与解析正常；若运行时仍 -32600，请完全重启 L3 或检查桌面 Sidecar 是否未注入 Key。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
