#!/usr/bin/env python3
"""
§4.3.5 / §12.2 离线路由沙盘：对样本句跑关键词袋 +（可选）打印配置阈值，不调用远程 Embedding。
用法（仓库根）:
  python scripts/intent_gateway_route_sandbox.py
  python scripts/intent_gateway_route_sandbox.py --samples "查一下BI日报" "停止招聘"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description="Intent gateway keyword route sandbox")
    p.add_argument("--samples", nargs="*", help="Sample utterances")
    args = p.parse_args()
    samples = list(args.samples or [])
    if not samples:
        samples = [
            "帮我跑一下今天的BI日报",
            "停止所有招聘无人值守",
            "收网抓简历",
            "发个 JD 到 Boss",
            "@@@### 乱码测试",
        ]

    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.ood_signals import surface_ood_class
    from l3_node.intent_gateway.semantic_router import infer_semantic_route_hint

    cfg = get_intent_gateway_config()
    print("intent_gateway config snippet:", json.dumps({k: cfg.get(k) for k in sorted(cfg) if "embedding" in k or "ood" in k or "l1_" in k}, ensure_ascii=False))
    for s in samples:
        kw = infer_semantic_route_hint(s)
        ood_l, ood_s = surface_ood_class(s)
        print("---")
        print("text:", s[:200])
        print("keyword_hint:", json.dumps(kw, ensure_ascii=False))
        print("ood:", ood_l, ood_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
