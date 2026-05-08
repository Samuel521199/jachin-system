#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 物理层冒烟（L3 Agent + mcp-pyautogui-server）

**前提**
  - 目标页已由 **Puppeteer** 在**本机可见窗口**中打开（非无头或已 headless=false），且置于前台或清晰可截屏区域。
  - L3 已启动（HTTP 默认 ``http://127.0.0.1:18991``），``~/.jachin/mcp_servers.json`` 已配置 **mcp-pyautogui-server**。
  - 仓库已包含对 Puppeteer / PyAutoGUI 常见工具的前台同步豁免（``l3_node/foreground_tool_policy.py`` 中 ``long_running_tool_ids``），
    否则会出现 ``foreground_sync_budget_exceeded``（默认 5s）。

**能力说明（与需求对齐）**
  - 当前 PyPI ``mcp-pyautogui-server`` **没有** ``locateCenterOnScreen`` MCP 工具；图像定位需：
    - 由**多模态模型**根据 ``screenshot`` 的 Observation（若管线带图）判断坐标；或
    - 使用环境变量提供的**标定坐标**（见下方）；或
    - 在仓库内扩展 MCP 服务自行封装 ``locateOnScreen``（本脚本不实现）。
  - 所有点击须 **先 move_mouse 再 click_mouse**（与 pyautogui 真实轨迹一致）。

**用法（仓库根）**
  python scripts/test_k11_pyautogui_physical_smoke_l3.py
  python scripts/test_k11_pyautogui_physical_smoke_l3.py --verbose --max-iterations 24
  set K11_PLAY_NOW_X=1200&& set K11_PLAY_NOW_Y=640&& python scripts/test_k11_pyautogui_physical_smoke_l3.py

**调高全局同步预算（可选）** 编辑 ``~/.jachin/nexus_config.json``：
  ``"foreground_tools": { "sync_timeout_sec": 45 }``
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass


def _http_post_json(
    url: str, body: dict[str, Any], *, timeout: float
) -> tuple[int, dict[str, Any] | str]:
    try:
        import urllib.error
        import urllib.request
    except ImportError:
        return 0, "urllib_unavailable"

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        code = e.code
    except Exception as e:
        return 0, f"request_failed:{e}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return code, f"not_dict:{raw[:800]}"
        return code, parsed
    except json.JSONDecodeError:
        return code, raw


def _build_user_input(*, context_url: str, play_xy: tuple[int, int] | None, card_xy: tuple[int, int] | None) -> str:
    calib_lines: list[str] = []
    if play_xy:
        calib_lines.append(
            f"- 用户标定 **Play Now!** 屏幕坐标参考（像素）: x={play_xy[0]}, y={play_xy[1]}。"
            "请先用 move_mouse 移至此坐标附近，再 click_mouse；若页面布局变化可微调。"
        )
    if card_xy:
        calib_lines.append(
            f"- 用户标定 **首张核心游戏卡片** 点击参考坐标: x={card_xy[0]}, y={card_xy[1]}。"
        )
    calib = "\n".join(calib_lines) if calib_lines else (
        "- 未提供标定坐标：请依赖 **screenshot** 与当前轮次多模态能力推断可点区域；"
        "若 Observation 无图像仅文本，请如实说明并尝试保守的中心/网格试探（记录每步 observation）。"
    )

    return f"""你是 Jachin **物理执行器**：只能用工具清单中与 **PyAutoGUI / 桌面自动化** 相关的 MCP 工具
（id 通常为 mcp:screenshot、mcp:move_mouse、mcp:click_mouse、mcp:double_click_mouse、mcp:scroll_down 等，
以本回合系统注入的清单为准）。禁止编造未列出的工具名（例如 locateCenterOnScreen 若不在清单中则**不得**假设存在）。

**上下文**
- 浏览器已由 Puppeteer 打开，当前应显示 K11 / KalaroKo 类游戏平台相关页面。
- 参考 URL（仅说明用）: {context_url}

**标定与图像**
{calib}

**操作规范**
1. 每一次点击：必须先 **mcp:move_mouse** 到目标 (x,y)，再 **mcp:click_mouse**（禁止不移动直接点未知位置，除非当前光标已在目标）。
2. 需要观察界面时调用 **mcp:screenshot**；若识别失败，在 evidence 中写明并再截一张；可配合 **scroll_down** / **scroll_up** 找按钮。
3. 若工具返回 error / failsafe（鼠标触边），在 detail 中记录并尝试恢复（移回屏幕中部再继续）。

**验收项（对应冒烟表 P0）**
A. **主按钮可用（物理）**：在可见浏览器窗口中找到并物理点击 **Play Now!**（或等价主 CTA），依据截图/界面变化判断 pass/fail。
B. **关键卡片点击**：至少点击 **一个**核心游戏卡片区域，确认进入下一层流程（新画面/加载/Canvas 变化），依据截图判断。
C. **各游戏正常运行（物理闭环，尽力）**：进入游戏后寻找 **开始/Start/Play** 类控件并物理点击；随后根据多轮截图判断是否出现 **结算/得分/Score/Game Over/You Win** 等**可感知**终局特征。若单轮内无法完成整局，pass 可为 false，但 detail 须写清卡在哪一步。

**最终回答**
最后一轮必须输出**一段可解析的 JSON**（可放在 markdown 代码块内），键名勿改：
```json
{{
  "test_play_now_physical": {{ "pass": true, "detail": "…" }},
  "test_key_card_physical": {{ "pass": true, "detail": "…" }},
  "test_game_round_physical": {{ "pass": true, "detail": "…" }},
  "evidence": ["工具名与关键 observation 摘要"],
  "limitations": ["如：无 locateCenterOnScreen、Observation 无图像等"]
}}
```
若某步失败，对应 pass 填 false，detail 写明错误（含是否出现 foreground_sync_budget_exceeded 等）。"""


def _parse_report(answer: str) -> dict[str, Any] | None:
    if not (answer or "").strip():
        return None
    text = answer.strip()
    candidates: list[str] = []
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        candidates.append(m.group(1).strip())
    lb, rb = text.find("{"), text.rfind("}")
    if lb != -1 and rb > lb:
        candidates.append(text[lb : rb + 1])
    for frag in candidates:
        try:
            o = json.loads(frag)
            if isinstance(o, dict) and "test_play_now_physical" in o:
                return o
        except json.JSONDecodeError:
            continue
    return None


def _log_payload_verbose(payload: dict[str, Any], *, verbose: bool) -> None:
    if not verbose:
        return
    keys = sorted(payload.keys())
    print(f"[k11-pyauto-l3] response keys: {keys}", flush=True)
    for k in ("run_id", "trace", "iterations", "tool_calls", "error", "model"):
        if k in payload:
            v = payload[k]
            s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            print(f"[k11-pyauto-l3] {k}: {s[:2000]}{'…' if len(s) > 2000 else ''}", flush=True)
    ans = payload.get("answer")
    if isinstance(ans, str):
        print(f"[k11-pyauto-l3] answer_len={len(ans)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="K11 物理冒烟：L3 Agent + PyAutoGUI MCP")
    ap.add_argument(
        "--l3-base",
        default=os.environ.get("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991"),
        help="L3 HTTP 根地址",
    )
    ap.add_argument("--max-iterations", type=int, default=24)
    ap.add_argument(
        "--context-url",
        default="https://www.kalaroko.com/",
        help="写入提示词的参考 URL（浏览器应已打开等价页面）",
    )
    ap.add_argument("--play-now-x", type=int, default=None)
    ap.add_argument("--play-now-y", type=int, default=None)
    ap.add_argument("--card-x", type=int, default=None)
    ap.add_argument("--card-y", type=int, default=None)
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    play_xy = None
    if args.play_now_x is not None and args.play_now_y is not None:
        play_xy = (args.play_now_x, args.play_now_y)
    elif os.environ.get("K11_PLAY_NOW_X") and os.environ.get("K11_PLAY_NOW_Y"):
        try:
            play_xy = (int(os.environ["K11_PLAY_NOW_X"]), int(os.environ["K11_PLAY_NOW_Y"]))
        except ValueError:
            pass

    card_xy = None
    if args.card_x is not None and args.card_y is not None:
        card_xy = (args.card_x, args.card_y)
    elif os.environ.get("K11_CARD_X") and os.environ.get("K11_CARD_Y"):
        try:
            card_xy = (int(os.environ["K11_CARD_X"]), int(os.environ["K11_CARD_Y"]))
        except ValueError:
            pass

    user_input = _build_user_input(
        context_url=args.context_url,
        play_xy=play_xy,
        card_xy=card_xy,
    )

    base = args.l3_base.rstrip("/")
    url = f"{base}/api/v3/agent/run"
    body: dict[str, Any] = {
        "user_input": user_input,
        "max_iterations": args.max_iterations,
        "implicit_attribution": {"channel": "http_k11_pyautogui_physical_smoke"},
    }

    print(f"[k11-pyauto-l3] POST {url} max_iterations={args.max_iterations}", flush=True)
    if args.verbose:
        print(f"[k11-pyauto-l3] user_input chars={len(user_input)}", flush=True)

    code, payload = _http_post_json(url, body, timeout=900.0)

    if isinstance(payload, str):
        print(f"[k11-pyauto-l3] 请求失败: {payload}", file=sys.stderr)
        return 2

    _log_payload_verbose(payload, verbose=args.verbose)

    if code == 503 or (
        isinstance(payload.get("error"), str) and "尚未就绪" in str(payload.get("error"))
    ):
        print("[k11-pyauto-l3] L3 Agent 引擎未就绪，请先启动 L3。", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000], file=sys.stderr)
        return 1
    if code >= 400:
        print(f"[k11-pyauto-l3] HTTP {code}", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:8000], file=sys.stderr)
        return 1
    if payload.get("error"):
        print(f"[k11-pyauto-l3] L3 error: {payload.get('error')}", file=sys.stderr)
        return 1

    answer = (payload.get("answer") or "").strip()
    print("\n[k11-pyauto-l3] ========== L3 原始回答 ==========\n", flush=True)
    print(answer[:32000], flush=True)
    if len(answer) > 32000:
        print("\n…（已截断）", flush=True)

    report = _parse_report(answer)
    print("\n[k11-pyauto-l3] ========== 结构化汇总 ==========", flush=True)
    if not report:
        print("  （未能解析 JSON；请查看原始回答）", flush=True)
        if "foreground_sync_budget" in answer:
            print(
                "\n[k11-pyauto-l3] 提示：回答中出现 foreground_sync_budget_exceeded。"
                "请确认已更新 foreground_tool_policy 中 PyAutoGUI/Puppeteer 工具豁免，"
                "或在 nexus_config.json 增大 sync_timeout_sec。",
                flush=True,
            )
        return 1

    for key in (
        "test_play_now_physical",
        "test_key_card_physical",
        "test_game_round_physical",
    ):
        block = report.get(key)
        if isinstance(block, dict):
            p = block.get("pass")
            d = (block.get("detail") or "").strip()
            print(f"  {key}: {'PASS' if p else 'FAIL'} — {d}", flush=True)
    ev = report.get("evidence")
    if isinstance(ev, list):
        for i, e in enumerate(ev[:24], 1):
            print(f"  证据{i}: {e}", flush=True)
    lim = report.get("limitations")
    if isinstance(lim, list) and lim:
        print("  限制:", flush=True)
        for x in lim[:12]:
            print(f"    - {x}", flush=True)

    p1 = bool((report.get("test_play_now_physical") or {}).get("pass"))
    p2 = bool((report.get("test_key_card_physical") or {}).get("pass"))
    p3 = bool((report.get("test_game_round_physical") or {}).get("pass"))
    all_ok = p1 and p2 and p3
    print(f"\n[k11-pyauto-l3] 总评: {'三项均通过' if all_ok else '存在未通过项'}", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
