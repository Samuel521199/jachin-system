#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 平台冒烟 · 5 款游戏状态机轻量测（大厅扩展包：Tongits + 四款休闲/社交）

**与** ``test_k11_smoke_games_state_machine_playwright.py`` **同源实现**（import 后改游戏表与场景），
对齐《K11_平台冒烟测试用例》P0 行 42-43：可运行性 + 金币粗测。

本清单（5 款）：
  Tongits King · Bingo Showdown · Infinity 9 Ball · Color Blitz Social ·
  Royal Pusoy

飞书完成通知卡片：与母脚本相同 ``send_k11_smoke_lark_notification``（``--no-lark-report`` 可关）。

用法::

  python scripts/test_k11_smoke_games_state_machine_six_card_playwright.py
  python scripts/test_k11_smoke_games_state_machine_six_card_playwright.py --single --game bingo_showdown

环境：``KALAROKO_CDP_ENDPOINT``、``pip install playwright``
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except Exception:
    pass

os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin,"
    "herontest.xin,www.herontest.xin,gweb.herontest.xin",
)

# —— 本套件固定顺序与大厅展示名（与 _k11_lobby_click_selector_string 网格标题格一致）——
SIX_CARD_GAME_ORDER: tuple[str, ...] = (
    "tongits_king",
    "bingo_showdown",
    "infinity_9_ball",
    "color_blitz_social",
    "royal_pusoy",
)

SIX_CARD_LOBBY_DISPLAY: dict[str, str] = {
    "tongits_king": "Tongits King",
    "bingo_showdown": "Bingo Showdown",
    "infinity_9_ball": "Infinity 9 Ball",
    "color_blitz_social": "Color Blitz Social",
    "royal_pusoy": "Royal Pusoy",
}

# 单局「智能等待」上限的每款默认秒数（与母脚本同键 GAME_DURATION_SEC）
SIX_CARD_DURATION_SEC: dict[str, int] = {
    "tongits_king": 95,
    "bingo_showdown": 95,
    "infinity_9_ball": 95,
    "color_blitz_social": 95,
    "royal_pusoy": 95,
}

_SCHEMA_TAG = "k11_smoke_state_machine_six_card/v1"


def _load_state_machine_module() -> Any:
    path = ROOT / "scripts" / "test_k11_smoke_games_state_machine_playwright.py"
    name = "k11_smoke_games_state_machine_base"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    # 必须在 exec_module 前登记，否则 @dataclass 解析类型时 sys.modules[name] 为 None 会报错
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tongits_scenario_from_mcp() -> dict[str, Any]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        KALAROKO_DEFAULT_SCENARIOS,
    )

    for s in KALAROKO_DEFAULT_SCENARIOS:
        if str(s.get("name") or "") == "tongits_king":
            return dict(s)
    raise RuntimeError("KALAROKO_DEFAULT_SCENARIOS 中缺少 tongits_king")


def _extra_four_scenarios(target_url: str) -> dict[str, dict[str, Any]]:
    """
    与 ``test_k11_extra_games_shell_smoke_playwright.EXTRA_GAMES_SCENARIOS`` 同形；
    start_url 在运行时可被 home 覆盖。
    """
    return {
        "bingo_showdown": {
            "name": "bingo_showdown",
            "start_url": target_url,
            "click_selector": r"text=/Bingo\s*Showdown/i",
            "prefer_last_on_ambiguous_entry": True,
            "entry_wait_until": "domcontentloaded",
            "click_timeout_ms": 9000,
            "wait_until": "domcontentloaded",
            "timeout_ms": 22_000,
        },
        "infinity_9_ball": {
            "name": "infinity_9_ball",
            "start_url": target_url,
            "click_selector": r"text=/Infinity\s*9\s*Ball/i",
            "prefer_last_on_ambiguous_entry": True,
            "entry_wait_until": "domcontentloaded",
            "click_timeout_ms": 9000,
            "wait_until": "domcontentloaded",
            "timeout_ms": 22_000,
        },
        "color_blitz_social": {
            "name": "color_blitz_social",
            "start_url": target_url,
            "click_selector": r"text=/Color\s*Blitz\s*Social/i",
            "prefer_last_on_ambiguous_entry": True,
            "entry_wait_until": "domcontentloaded",
            "click_timeout_ms": 9000,
            "wait_until": "domcontentloaded",
            "timeout_ms": 22_000,
        },
        "royal_pusoy": {
            "name": "royal_pusoy",
            "start_url": target_url,
            "click_selector": r"text=/Royal\s*Pusoy/i",
            "prefer_last_on_ambiguous_entry": True,
            "entry_wait_until": "domcontentloaded",
            "click_timeout_ms": 9000,
            "wait_until": "domcontentloaded",
            "timeout_ms": 22_000,
        },
    }


def _patch_state_machine_module(sm: Any, *, default_target: str) -> None:
    """覆写母脚本全局表，使 _run_one_game / argparse 使用本清单。"""
    scen_map: dict[str, dict[str, Any]] = {}
    scen_map["tongits_king"] = _tongits_scenario_from_mcp()
    for k, v in _extra_four_scenarios(default_target).items():
        scen_map[k] = v

    orig_sf = sm._scenario_for_game

    def _scenario_for_six(name: str) -> dict[str, Any]:
        g = (name or "").strip()
        if g in scen_map:
            return dict(scen_map[g])
        return orig_sf(name)

    sm._GAME_ORDER = list(SIX_CARD_GAME_ORDER)
    sm._GAME_LOBBY_DISPLAY = dict(SIX_CARD_LOBBY_DISPLAY)
    merged_dur = dict(sm.GAME_DURATION_SEC)
    merged_dur.update(SIX_CARD_DURATION_SEC)
    sm.GAME_DURATION_SEC = merged_dur
    sm._scenario_for_game = _scenario_for_six


def _maybe_bump_json_schema(args: argparse.Namespace) -> None:
    raw = (getattr(args, "json_out", None) or "").strip()
    if not raw:
        return
    p = Path(raw)
    if not p.is_file():
        return
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["schema"] = _SCHEMA_TAG
        doc["suite"] = "six_card_lobby"
        doc["games_order"] = list(SIX_CARD_GAME_ORDER)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _build_parser(sm: Any) -> argparse.ArgumentParser:
    g0 = SIX_CARD_GAME_ORDER[0]
    ap = argparse.ArgumentParser(
        description="K11 状态机轻量 5 款（Tongits + Bingo/9Ball/Blitz/Pusoy）可运行 + 金币"
    )
    ap.add_argument("--target-url", default=sm.DEFAULT_TARGET)
    ap.add_argument("--cdp-http", default="")
    ap.add_argument(
        "--game",
        default=g0,
        choices=list(SIX_CARD_GAME_ORDER),
    )
    ap.add_argument("--single", action="store_true", help="只跑 --game 一款")
    ap.add_argument("--require-existing-tab", action="store_true")
    ap.add_argument("--json-out", default="", help="结果 JSON 路径")
    ap.add_argument(
        "--screenshot-dir",
        default="",
        help="失败/异常时截图目录，默认不保存",
    )
    ap.add_argument(
        "--entry-wait-sec",
        type=float,
        default=None,
        help="进壳（URL/iframe）最长等待，默认 90 或 K11_SM_ENTRY_TIMEOUT",
    )
    ap.add_argument(
        "--pre-wait-sec",
        type=float,
        default=None,
        help="进壳后预载秒数，默认 10 或 K11_SM_PRE_WAIT",
    )
    ap.add_argument(
        "--play-wait-sec",
        type=float,
        default=None,
        help="单款「游玩+轮询」硬上限（秒，与 K11_SM_PLAY_CAP 取小）",
    )
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--no-exec-journal",
        action="store_true",
        help="不写详细执行流水日志",
    )
    ap.add_argument(
        "--no-lark-report",
        action="store_true",
        help="不发送飞书完成通知",
    )
    ap.add_argument(
        "--lark-wiki-url",
        default="",
        help="飞书 Wiki 链接（默认同母脚本/环境变量）",
    )
    return ap


async def _async_entry(sm: Any, args: argparse.Namespace) -> int:
    rc = await sm._async_main(args)
    _maybe_bump_json_schema(args)
    return int(rc)


def main() -> int:
    sm = _load_state_machine_module()
    _patch_state_machine_module(sm, default_target=str(sm.DEFAULT_TARGET).strip())
    ap = _build_parser(sm)
    args = ap.parse_args()
    print(
        "———————— K11 状态机 · 5 款（Tongits + 扩展四款）————————",
        flush=True,
    )
    print("游戏: " + " · ".join(SIX_CARD_LOBBY_DISPLAY[g] for g in SIX_CARD_GAME_ORDER), flush=True)
    print("", flush=True)
    return asyncio.run(_async_entry(sm, args))


if __name__ == "__main__":
    raise SystemExit(main())
