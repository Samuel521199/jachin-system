#!/usr/bin/env python3
"""探测哪个 app 能发到哪个群。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from l3_node.jachin_config import load_mcp_config
from l3_node.tools.pmo_bitable_watch import _load_watch_config, send_watch_notification

CHAT = "oc_b1b9cff6804517c79b7f5a617ab30483"
MSG = "**PMO 推送探针**\n若看到这条，说明该应用可以发到这个群。"

def main() -> int:
    pmo = _load_watch_config()
    notifier = load_mcp_config("atom_lark_notifier", project_root=_ROOT)
    candidates = [
        ("pmo_bitable_watch.yaml", CHAT, pmo.get("app_id"), pmo.get("app_secret")),
        ("atom_lark_notifier", CHAT, notifier.get("app_id"), notifier.get("app_secret")),
    ]
    for name, chat_id, aid, sec in candidates:
        aid_s = str(aid or "").strip()
        sec_s = str(sec or "").strip()
        if aid_s.startswith("${") or not aid_s or not sec_s:
            print(f"[SKIP] {name}: 凭证未配置")
            continue
        r = send_watch_notification(MSG, chat_id=chat_id, title="PMO 探针", app_id=aid_s, app_secret=sec_s)
        print(f"[{name}] app={aid_s[:16]}… chat={chat_id}")
        print(json.dumps(r, ensure_ascii=False, indent=2))
        print()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
