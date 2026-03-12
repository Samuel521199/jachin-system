#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试推荐牛人自动打招呼。

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 在 Chrome 中登录 Boss 直聘
  3. 编辑 data\\jd_to_publish.json 填写 JD 内容
  4. 运行本脚本（建议先在浏览器打开「推荐牛人」页面）
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

from tools.atom_greet_recommend_boss import atom_greet_recommend_boss, load_jd_config

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="", help="JD 配置文件路径，如 data/后端工程师/jd.json")
    p.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome 调试端口")
    p.add_argument("--max-greet", type=int, default=0, help="最多打招呼人数，默认 3（0=用默认值）")
    args = p.parse_args()

    config_path = (args.config or "").strip()
    # 相对路径从插件根目录解析
    if config_path and not Path(config_path).is_absolute() and not Path(config_path).exists():
        resolved = ROOT / config_path.replace("\\", "/")
        if resolved.exists():
            config_path = str(resolved)

    jd = load_jd_config(config_path)
    if not jd:
        print("警告: 未加载到 JD 配置，将使用默认筛选条件")
    else:
        print("已加载 JD:", jd.get("job_title", ""), "学历:", jd.get("education", ""), "经验:", jd.get("experience", ""))

    result = atom_greet_recommend_boss(
        cdp_url=args.cdp,
        jd_config_path=config_path or "",
        max_greet_per_run=args.max_greet if args.max_greet > 0 else 0,
    )

    print("成功:", result.get("success"))
    print("打招呼人数:", result.get("greeted_count", 0))
    print("跳过(已沟通):", result.get("skipped_chat_history", 0))
    print("跳过(初筛不通过):", result.get("skipped_low_score", 0))
    if result.get("error"):
        print("错误:", result["error"])
