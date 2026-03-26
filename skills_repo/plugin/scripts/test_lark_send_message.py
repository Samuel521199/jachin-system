#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Lark 机器人发言：发送固定文案或 AI 生成回复。

用法：
  # 发送固定文案
  python scripts\test_lark_send_message.py --text "你好，我是 HR 招聘辅助机器人"
  # 用百炼生成回复后发送
  python scripts\test_lark_send_message.py --prompt "用户问：现在有几个候选人？" --llm
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "com.jachin.hr.recruitment"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from tools.atom_lark_send_message import atom_lark_send_message

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--text", default="", help="直接发送的文案")
    p.add_argument("--prompt", default="", help="用户提问（配合 --llm 用百炼生成）")
    p.add_argument("--llm", action="store_true", help="用阿里百炼生成回复后发送")
    p.add_argument("--chat-id", default="", help="目标 chat_id，不填用 LARK_CHAT_ID")
    args = p.parse_args()

    if not args.text and not args.prompt:
        args.text = "你好，我是 HR 招聘辅助机器人～"
    out = atom_lark_send_message(
        text=args.text,
        prompt=args.prompt,
        use_llm=args.llm,
        chat_id=args.chat_id,
    )
    print("success:", out.get("success"))
    if out.get("message"):
        msg = out["message"]
        try:
            print("已发送:", (msg[:80] + "..." if len(msg) > 80 else msg))
        except UnicodeEncodeError:
            print("已发送: [含特殊字符，长度 %d]" % len(msg))
    if out.get("error"):
        print("error:", out["error"])

    sys.exit(0 if out.get("success") else 1)
