#!/usr/bin/env python3
"""
自然周/自然月 · 留存对比双卡片

用法（项目根）:
  python scripts/run_bi_natural_retention.py --weekly
  python scripts/run_bi_natural_retention.py --monthly
  python scripts/run_bi_natural_retention.py --weekly --skip-collect   # 仅推送已有 raw_natural

定时建议:
  周一 9:00  --weekly
  每月1日 9:15  --monthly

配置: config/skills/com.jachin.bi.natural_retention/bi_natural.yaml
"""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(root / ".env", encoding="utf-8")
except ImportError:
    pass
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from l3_node.skills.bi.bi_natural.main_skill import run_bi_natural_retention_cli


if __name__ == "__main__":
    sys.exit(run_bi_natural_retention_cli())
