#!/usr/bin/env python3
"""测试 L3 本地 boss_harvester 逻辑（直接调用，不经过 MCP）"""
import argparse
import json
import sys
from pathlib import Path

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"))

L3_VOLUME_ROOT = Path.home() / ".jachin" / "client_volumes"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--job", default="Java_杭州 4-6K")
    p.add_argument("--max", type=int, default=5)
    p.add_argument("--no-request", action="store_true", help="Disable request_if_no_resume")
    args = p.parse_args()

    save_dir = L3_VOLUME_ROOT / "global_resume_pool"
    save_dir.mkdir(parents=True, exist_ok=True)

    from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow

    r = atom_inbox_harvester_full_flow(
        job_text=args.job,
        max_items=args.max,
        save_dir=str(save_dir),
        filter_tab="全部",
        request_if_no_resume=not args.no_request,
    )
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
