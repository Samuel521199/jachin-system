"""
PMO 资源预警：手动立即触发一次巡检（绕过定时调度，适合测试与临时检查）。

用法：
  python scripts/run_pmo_resource_monitor_once.py            # 默认周三口径
  python scripts/run_pmo_resource_monitor_once.py --kind thu # 周四口径
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 加载 .env
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
    load_dotenv(Path.home() / ".jachin" / ".env", override=False)
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO 资源预警：手动触发一次巡检")
    ap.add_argument(
        "--kind",
        choices=("wed", "thu", "w", "t", "wednesday", "thursday"),
        default="wed",
        help="巡检口径：wed=延期+偏闲（周三语义），thu=延期+进度落后（周四语义）",
    )
    args = ap.parse_args()

    from l3_node.jobs.pmo_copilot_scheduler import run_pmo_resource_monitor_once

    print(f"[run_pmo_resource_monitor_once] 触发巡检 kind={args.kind} …")
    out = run_pmo_resource_monitor_once(args.kind)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
