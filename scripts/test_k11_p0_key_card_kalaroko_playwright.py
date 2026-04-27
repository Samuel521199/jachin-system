#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 · P0 关键卡片 + 多游戏可玩 + 金币（与 Kalaroko E2E / MCP 同源流程）

**对齐文档**《K11_平台冒烟测试用例》：

- **关键卡片点击**：至少 1 个核心游戏卡片能进入下一层（game-frame 壳）。
- **各游戏正常运行**：在进 **game-frame** 后，**不操作牌面**，在时限内轮询 **正文 + 各 frame 按钮/aria-live**，
  并**间歇尝试点掉**「确定/继续/领取」等结算遮罩；命中终局启发式或 **URL 离开 game-frame** 后，
  略等再采壳内余额，**KK/Exit 或战术撤离** 回大厅。默认 **严格自然终局**：不会在未见结算文案时
  点「Exit/回大厅」，也不启用易在约 90s 误判的 **短正文/长稳** 兜底（旧行为见 ``--lenient-round-end``）。
  纯 Canvas、无文案例仍可能 **FAIL/超时**（可调 ``--round-max-wait-sec`` 或放宽终局判定）。
- **游戏金币同步**：**每局进房前**采大厅余额；**终局后、回大厅前**在壳内再采一次；**回大厅后**再采。逐局比对可解析数字，
  失败条件：该局已完局但前后数字缺失等。

与 ``scripts/test_kalaroko_default_scenarios_e2e.py`` 一致：复用
``KALAROKO_DEFAULT_SCENARIOS``、``_diagnose_and_click_kalaroko_game_entry``、
``_game_deep_wait_after_goto`` 等（``l3_client/.../mcp_kalaroko_monitor.py``）。

**与云端 E2E 的差异**：本脚本不跑 ``fetch_api_health`` / ``manage_perf_history``，专注浏览器侧三条 P0。

**终局判定策略（与常见工程方案对齐）**：

1. **黑盒 UI 观测（方案一）**：主循环 + 总超时（``--round-max-wait-sec`` / ``K11_P0_ROUND_MAX_WAIT_SEC``），
   轮询间隔由 ``--round-poll-sec`` 控制（可设 5～10s 降低频率）；聚合 URL/title、正文、按钮/shadow、结算类关键词。
2. **旁路余额（方案二）**：生产环境若以 **接口/DB 余额快照** 为准，建议在 L3 侧用 MCP/HTTP 做「开局前 balance_initial → 结束后 balance_final」，
   本 Playwright 脚本仍覆盖「进壳 + UI 终局启发式 + 大厅粗采样」，二者可并存。
3. **window 埋点（方案三）**：若前端在终局时写入 ``window.gameStatus='finished'`` 等，本脚本会探测
   ``gameStatus``、``__JACHIN_GAME_STATUS__``、``__KALAROKO_ROUND_STATUS__``、``jachinRoundEnded`` 等（见 ``_probe_window_round_status``）。

前置：
  - ``KALAROKO_CDP_ENDPOINT`` 或 ``--cdp-http``
  - ``pip install playwright``

用法（仓库根）::

  python scripts/test_k11_p0_key_card_kalaroko_playwright.py
  python scripts/test_k11_p0_key_card_kalaroko_playwright.py --no-mobile-viewport
  python scripts/test_k11_p0_key_card_kalaroko_playwright.py --single --game tongits_king
  python scripts/test_k11_p0_key_card_kalaroko_playwright.py -v --json-out out/k11_p0_key.json
  set K11_P0_ROUND_MAX_WAIT_SEC=900
  python scripts/test_k11_p0_key_card_kalaroko_playwright.py --round-max-wait-sec 900

**视口（手机尺寸）**：默认在取到目标页后调用 Playwright ``set_viewport_size`` 为 **459×851**
（与 Chrome F12「设备/响应式」下常见直板宽度同级；可 ``--viewport 390x844`` 或环境 ``K11_P0_VIEWPORT`` 覆盖；``--no-mobile-viewport`` 保留桌面视口）。

输出：每款游戏有独立 **PASS/FAIL**；与《K11_平台冒烟测试用例》对应的三条 P0
（关键卡片点击 / 各游戏正常运行 / 游戏金币同步）在控制台与 **JSON** 中分项列出。  
**总评**仅作汇总，不以单行代替分项。

退出码：0 全部项满足脚本规则；1 有 FAIL；2 环境/CDP；3 未捕获异常。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
except ImportError:
    pass
except OSError:
    pass

os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin",
)

DEFAULT_TARGET = "https://www.kalaroko.com/"

# 与 Chrome 开发者工具中「设备工具条 / 响应式」下常见直板机宽度一致（用户示例约 459×851），便于大厅与 game-frame 走移动布局
DEFAULT_VIEWPORT_W = 459
DEFAULT_VIEWPORT_H = 851


def _parse_viewport_wh(text: str | None) -> tuple[int, int] | None:
    """
    解析 "459x851" / "459×851"；宽高至少 200，避免误配。
    """
    if not text or not str(text).strip():
        return None
    t = str(text).strip().lower().replace("×", "x")
    if "x" not in t:
        return None
    a, b = t.split("x", 1)
    try:
        w = int(a.strip())
        h = int(b.strip())
    except ValueError:
        return None
    if w < 200 or h < 200 or w > 5000 or h > 5000:
        return None
    return w, h


def _default_viewport_str() -> str:
    v = (os.environ.get("K11_P0_VIEWPORT") or "").strip()
    if v:
        return v
    return f"{DEFAULT_VIEWPORT_W}x{DEFAULT_VIEWPORT_H}"


async def _apply_mobile_viewport(
    page: Any,
    *,
    width: int,
    height: int,
    log: Callable[[str], None],
) -> None:
    """
    将 **布局视口** 固定为直板机尺寸（等价于在页面按 F12 后打开设备/响应式并选用相近宽高的效果），
    不强制改本机窗口像素；Playwright 使用 ``set_viewport_size``。
    """
    try:
        await page.set_viewport_size({"width": int(width), "height": int(height)})
        log(f"视口已设为手机测试尺寸：{int(width)}×{int(height)}（与 K11 统合/大厅移动布局类场景对齐）")
    except Exception as e:
        log(f"  [warn] set_viewport_size 失败: {e}；后续仍用浏览器当前视口")

# 与 MCP 内 ``_METRICS_JS`` 无关：仅用于大厅 / 壳内「余额/金币」粗采样（勿当财务审计）
# 余额常在 header、nav、shadow 外层的兄弟节点；多扫 class / data 与正文正则
_LOBBY_WALLET_SNIFF_JS = r"""() => {
  const out = [];
  const push = (s) => {
    const t = (s || '').replace(/\s+/g, ' ').trim();
    if (t && /\d/.test(t)) out.push(t.slice(0, 140));
  };
  for (const sel of [
    '[class*="balance" i]', '[class*="coin" i]', '[class*="wallet" i]',
    '[class*="Currency" i]', '[class*="currency" i]', '[class*="money" i]',
    '[class*="credit" i]', '[class*="gold" i]', '[class*="chip" i]', '[class*="php" i]',
    'header [class*="gold" i]', 'nav', 'header', '[data-balance]', '[data-coin]',
    '[data-amount]', '[data-testid*="balance" i]', '[data-testid*="coin" i]',
    '[class*="settle" i]', '[class*="reward" i]', '[class*="win" i]', '[class*="chips" i]',
    '[class*="user" i]', '[class*="profile" i]', '[class*="asset" i]', '[class*="fund" i]'
  ]) {
    try {
      document.querySelectorAll(sel).forEach((el) => push(el.innerText));
    } catch (e) {}
  }
  try {
    if (document.body) {
      const t = (document.body.innerText || '').slice(0, 9000);
      const patterns = [
        /(?:₱|PHP|JCoins?|Coins?|Balance|Wallet)\s*[:\s]*[\d,]+(?:\.[\d]+)?/ig,
        /[\d,]+(?:\.[\d]+)?\s*(?:PHP|₱|Coins?|JCoins?)/ig,
        /(?:余额|金币|钱包|财产)\s*[：:\s]*[\d,]+(?:\.[\d]+)?/g
      ];
      for (const re of patterns) {
        const m = t.match(re);
        if (m) m.slice(0, 10).forEach((x) => push(x));
      }
    }
  } catch (e) {}
  return { hints: [...new Set(out)].slice(0, 28), href: (location && location.href) || '' };
}"""

# 对局自然结束：多语言启发式；纯 Canvas 可辅以正文「长稳」兜底（见 _wait_for_natural_round_end）
# 覆盖：结算层、胜负、领奖、离开桌台、重开/下一局等（含常见菲英 UI）
# color_blitz / royal_pusoy 在 `_round_end_pattern_for_game` 追加专用片段（三消与中式扑克文案差异大）
_ROUND_END_PATTERN_BASE = (
    r"(?:game\s*over|you\s*lose|you\s*win|defeat|victory|round\s*over|match\s*(?:over|end)|"
    r"settlement|play\s*again|back\s*to\s*lobby|return\s*to\s*lobby|leave\s*table|exit\s*room|"
    r"tap\s*to\s*continue|tap\s*anywhere|press\s*to\s*continue|watch\s*ad|free\s*coins|"
    r"\b(?:winner|winners?|loser|losers?|congratulations|better\s*luck|well\s*played|good\s*game)\b|"
    r"\b(?:draw|tie|rematch|collect|reward|rewards?|total\s*win|total\s*payout|gross\s*win|net\s*win|pot|jackpot|prize)\b|"
    r"(?:game|round|match|battle|table)\s*result\b|final\s*score\b|score\s*board\b|"
    r"you\s*'?ve\s*won|you\s*'?ve\s*lost|\bsettlement\b|\bsettle\b|"
    r"结算|胜利|失败|对局结束|牌局结束|本局|再玩|继续|返回|离开|重开|"
    r"恭喜|赢取|奖励|平局|下局|房间|退出|桌台|奖池|金币\s*[+\-]|"
    r"\b(?:panalo|talo)\b)"
)


def _round_end_pattern_for_game(game_id: str) -> str:
    gid = (game_id or "").strip().lower()
    extra = ""
    if gid == "color_blitz":
        # 三消 / Blitz：步数用尽、关卡结束、时间到等（与牌类「胜利」文案不同）
        extra = (
            r"|(?:no\s*moves|out\s*of\s*moves|moves?\s*remaining\s*:\s*0|moves?\s*:\s*0|"
            r"moves?\s*left\s*:\s*0|0\s*moves|level\s*(complete|cleared|up|failed|done)|"
            r"stage\s*(clear|cleared|complete)|well\s*done|nice\s*job|great\s*job|"
            r"try\s*again|shuffle|times?\s*up|time\s*'?s?\s*up|time\s*over|out\s*of\s*time|"
            r"game\s*complete|match\s*complete|all\s*clear|goal\s*(met|reached)|"
            r"combo\s*(end|finish|over)|mission\s*complete|objective\s*(met|complete)|"
            r"spectacular|awesome|perfect\s*clear|blitz\s*(over|end)|color\s*blitz)"
        )
    elif gid == "royal_pusoy":
        # 中式扑克 / Pusoy（避免 dragon/armada 等易在对局中出现的词误触终局）
        extra = (
            r"|(?:pusoy|chinese\s*poker|pattern\s*complete|special\s*pattern|"
            r"hand\s*(?:over|complete|finished|done)|round\s*result|final\s*hand|"
            r"showdown|compare\s*hands|compare\s*card|table\s*closed|room\s*closed|"
            r"waiting\s*for\s*(host|players)|full\s*house\s*bonus|"
            r"natural\s*winner|auto\s*-?\s*fold|all\s*fold|burn\s*card|deck\s*empty|"
            r"end\s*of\s*round|round\s*ended|scoring|points?\s*total|final\s*rank)"
        )
    elif gid == "tongits_king":
        extra = (
            r"|(?:tongits|melds?|sapaw|chow|fight|draw\s*deck|stock\s*empty|"
            r"last\s*card|declare|challenge|burned|dump\s*pile)"
        )
    return _ROUND_END_PATTERN_BASE + extra


def _compile_round_end_re(game_id: str) -> re.Pattern[str]:
    return re.compile(_round_end_pattern_for_game(game_id), re.I | re.M)


# 方案三：前端在 window 上暴露终局状态（大小写不敏感匹配值）
_JS_WINDOW_ROUND_STATUS = r"""() => {
  const keys = [
    'gameStatus', '__JACHIN_GAME_STATUS__', '__KALAROKO_ROUND_STATUS__',
    'jachinRoundEnded', 'kalarokoGamePhase', '__GAME_ROUND_ENDED__',
    '__K11_ROUND_FINISHED__'
  ];
  const out = [];
  try {
    const w = window;
    for (const k of keys) {
      try {
        if (w[k] === undefined || w[k] === null) continue;
        if (w[k] === true) { out.push(k + '=true'); continue; }
        if (w[k] === false) { out.push(k + '=false'); continue; }
        const v = String(w[k]).trim();
        if (v) out.push(k + '=' + v.slice(0, 96));
      } catch (e) {}
    }
  } catch (e) {}
  return out.join(' | ');
}"""

_FINISHED_WINDOW_VALUE_RE = re.compile(
    r"^(finished|ended|settlement|result|complete|done|round_over|roundover|post_game|postgame|"
    r"idle|lobby|settled|victory|defeat|success|fail|failed)$",
    re.I,
)


def _window_kv_indicates_finished(key: str, val: str) -> bool:
    k = (key or "").strip().lower()
    v = (val or "").strip()
    if not v:
        return False
    vl = v.lower()
    if vl in ("true", "1", "yes"):
        if k in (
            "jachinroundended",
            "__game_round_ended__",
            "__k11_round_finished__",
        ):
            return True
        if "ended" in k or "finished" in k:
            return True
    return bool(_FINISHED_WINDOW_VALUE_RE.match(v))


async def _probe_window_round_status(page: Any) -> tuple[bool, str]:
    """跨主文档与子 frame 探测 window 埋点（跨域 frame 会静默跳过）。"""
    try:
        frames = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        frames = [page.main_frame]
    for fr in frames:
        try:
            raw = await fr.evaluate(_JS_WINDOW_ROUND_STATUS)
        except Exception:
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        for part in raw.split("|"):
            chunk = part.strip()
            if "=" not in chunk:
                continue
            key, _, val = chunk.partition("=")
            key, val = key.strip(), val.strip()
            if _window_kv_indicates_finished(key, val):
                return True, f"{key}={val!r}"
    return False, ""


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")


def _load_p0_smoke_module() -> Any:
    path = ROOT / "scripts" / "test_k11_p0_platform_smoke_playwright.py"
    spec = importlib.util.spec_from_file_location("k11_p0_platform_smoke_playwright", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _game_names_from_mcp_defaults() -> list[str]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        KALAROKO_DEFAULT_SCENARIOS,
    )

    out: list[str] = []
    for s in KALAROKO_DEFAULT_SCENARIOS:
        n = str(s.get("name") or "").strip()
        if n and n != "homepage":
            out.append(n)
    return out


def _scenario_copy_for_game(game: str) -> dict[str, Any]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        KALAROKO_DEFAULT_SCENARIOS,
    )

    for s in KALAROKO_DEFAULT_SCENARIOS:
        if str(s.get("name") or "") == game:
            return dict(s)
    raise ValueError(f"未知游戏场景 {game!r}，可选：{', '.join(_game_names_from_mcp_defaults())}")


_BALANCE_HINT_PREF = re.compile(
    r"balance|wallet|coin|gold|chip|credit|₱|php|jc|jcoin|currency|余额|金币|钱包|财产",
    re.I,
)


def _first_number_from_hints(h: dict[str, Any]) -> tuple[float | None, str]:
    """从多源 hints 中择优抽「最像余额」的数字，避免点到 level/房间号等小数字。"""
    hints = h.get("hints")
    if not isinstance(hints, list) or not hints:
        return None, "无 hints 列表"
    num_re = re.compile(r"[\d,]+(?:\.\d+)?")
    scored: list[tuple[int, float, str]] = []
    for hint in hints:
        s = str(hint)
        flat = s.replace(",", "")
        for m in num_re.finditer(flat):
            raw = m.group(0).replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            if val < 0 or val > 1e14:
                continue
            score = 0
            if _BALANCE_HINT_PREF.search(s):
                score += 200
            if val >= 1000:
                score += 80
            elif val >= 100:
                score += 40
            elif val >= 10:
                score += 15
            else:
                score += 1
            try:
                score += int(min(25, math.log10(max(val, 1.0)) * 5))
            except ValueError:
                pass
            scored.append((score, val, s[:72]))
    if not scored:
        return None, "文案中无数字"
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best = scored[0]
    return best[1], f"自 hints 择优 score={best[0]} → {best[1]} ({best[2]!r})"


async def _snapshot_lobby_wallet(
    page: Any,
) -> dict[str, Any]:
    """主文档 + 全部可执行子 frame 合并采样（大厅余额常在壳内 iframe）。"""
    merged: list[str] = []
    hrefs: list[str] = []
    errors: list[str] = []
    try:
        frames = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        frames = []
    for fr in frames:
        try:
            raw = await fr.evaluate(_LOBBY_WALLET_SNIFF_JS)
            if isinstance(raw, dict):
                hs = raw.get("hints")
                if isinstance(hs, list):
                    merged.extend(str(x) for x in hs if x)
                h = raw.get("href")
                if isinstance(h, str) and h.strip():
                    hrefs.append(h.strip())
        except Exception as e:
            errors.append(str(e)[:100])
    out: dict[str, Any] = {
        "hints": list(dict.fromkeys(merged))[:36],
        "href": hrefs[0] if hrefs else "",
        "frames_sampled": len(frames),
    }
    if errors:
        out["_frame_errors"] = errors[:6]
    return out


async def _gather_frame_texts_for_end_probe(page: Any, max_total: int = 16000) -> str:
    """聚合主文档 + 子 frame 的 innerText，供终局关键词/稳态检测。"""
    chunks: list[str] = []
    try:
        main = await page.evaluate(
            "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
        )
        if isinstance(main, str) and main.strip():
            chunks.append(main[:8000])
    except Exception:
        pass
    try:
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                t = await fr.evaluate(
                    "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
                )
                if isinstance(t, str) and t.strip():
                    chunks.append(t[:4000])
            except Exception:
                continue
    except Exception:
        pass
    return "\n".join(chunks)[:max_total]


# 结算层按钮/aria 常不在大块 innerText 里；单独聚合以命中「确定/领取/继续」等
_JS_INTERACTIVE_END_SNIP = r"""() => {
  const parts = [];
  const push = (s) => {
    const t = (s || '').replace(/\s+/g, ' ').trim();
    if (t && t.length > 0 && t.length < 140) parts.push(t);
  };
  try {
    document.querySelectorAll(
      'button, [role="button"], a[href], [class*="btn" i], [class*="Button"]'
    ).forEach((el) => {
      push(el.innerText);
      const a = el.getAttribute && el.getAttribute('aria-label');
      if (a) push(a);
      const t2 = el.getAttribute && el.getAttribute('title');
      if (t2) push(t2);
    });
  } catch (e) {}
  try {
    document.querySelectorAll('[aria-live="polite"], [aria-live="assertive"]').forEach((el) => {
      push(el.innerText);
    });
  } catch (e) {}
  return parts.slice(0, 96).join(' | ');
}"""

# Color Blitz 等常用 Web Components：按钮文案在 shadow 内，普通 querySelector 扫不到
_JS_SHADOW_BUTTON_LABELS = r"""() => {
  const parts = [];
  const push = (s) => {
    const t = (s || '').replace(/\s+/g, ' ').trim();
    if (t && t.length > 0 && t.length < 140) parts.push(t);
  };
  const walk = (root, depth) => {
    if (depth > 6) return;
    try {
      root.querySelectorAll('button, [role="button"], a[href]').forEach((el) => {
        push(el.innerText);
        const a = el.getAttribute && el.getAttribute('aria-label');
        if (a) push(a);
      });
      root.querySelectorAll('*').forEach((el) => {
        try {
          if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
        } catch (e) {}
      });
    } catch (e) {}
  };
  walk(document, 0);
  return parts.slice(0, 80).join(' | ');
}"""


async def _gather_interactive_and_live_text(page: Any) -> str:
    chunks: list[str] = []
    try:
        frames = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        frames = [page.main_frame]
    for fr in frames:
        try:
            t = await fr.evaluate(_JS_INTERACTIVE_END_SNIP)
            if isinstance(t, str) and t.strip():
                chunks.append(t[:6000])
        except Exception:
            continue
        try:
            st = await fr.evaluate(_JS_SHADOW_BUTTON_LABELS)
            if isinstance(st, str) and st.strip():
                chunks.append(st[:4000])
        except Exception:
            continue
    return "\n".join(chunks)[:8000]


async def _build_round_end_probe_blob(page: Any) -> str:
    a = await _gather_frame_texts_for_end_probe(page)
    b = await _gather_interactive_and_live_text(page)
    return (a + "\n" + b).strip()[:20000]


async def _try_tap_settlement_dismiss(page: Any, log: Callable[[str], None]) -> bool:
    """
    终局后部分游戏用半透明层挡住结算文案；轻点「继续/确定/领取」等以露出可探测文案并便于后续采金币。
    限频由调用方控制，避免对局中误触。逐 frame 查找（引擎多在 iframe 内）。
    """
    patterns: list[re.Pattern[str]] = [
        re.compile(r"^继续$"),
        re.compile(r"^确定$"),
        re.compile(r"^好的$"),
        re.compile(r"^知道了$"),
        re.compile(r"^关闭$"),
        re.compile(r"^领取$"),
        re.compile(r"^收下$"),
        re.compile(r"^OK$", re.I),
        re.compile(r"^Continue$", re.I),
        re.compile(r"^CLAIM$", re.I),
        re.compile(r"^Claim$", re.I),
        re.compile(r"^CLOSE$", re.I),
        re.compile(r"^Close$", re.I),
        re.compile(r"^Next$", re.I),
        re.compile(r"^NEXT$", re.I),
        re.compile(r"^Confirm$", re.I),
        re.compile(r"^Got it$", re.I),
        re.compile(r"^GOT IT$", re.I),
    ]
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    if not frames:
        return False
    for fr in frames:
        for role_sel in ("button", '[role="button"]'):
            for pat in patterns:
                try:
                    loc = fr.locator(role_sel).filter(has_text=pat)
                    if await loc.count() == 0:
                        continue
                    btn = loc.first
                    if not await btn.is_visible(timeout=550):
                        continue
                    await btn.click(timeout=2200)
                    log(
                        f"  [round-wait] 已在某 frame 点击结算遮罩（{role_sel} {pat.pattern!r}）"
                    )
                    await page.wait_for_timeout(420)
                    return True
                except Exception:
                    continue
    return False


async def _try_tap_back_to_lobby(
    page: Any,
    log: Callable[[str], None],
    *,
    elapsed: float,
    min_wait_sec: float,
) -> bool:
    """
    对局已进行足够时间后，尝试点「回大厅 / Leave / Exit」等（多在三消第二局引擎或 Pusoy 终局菜单）。
    仅在 min_wait 后延后触发，降低对局中误触。
    """
    if elapsed < min_wait_sec + 28:
        return False
    patterns: list[re.Pattern[str]] = [
        re.compile(r"^Back\s*to\s*Lobby$", re.I),
        re.compile(r"^Return\s*to\s*Lobby$", re.I),
        re.compile(r"^Lobby$", re.I),
        re.compile(r"^Home$", re.I),
        re.compile(r"^Main\s*Menu$", re.I),
        re.compile(r"^Exit$", re.I),
        re.compile(r"^Quit$", re.I),
        re.compile(r"^Leave$", re.I),
        re.compile(r"^Leave\s*Table$", re.I),
        re.compile(r"^Leave\s*Room$", re.I),
        re.compile(r"^返回大厅$"),
        re.compile(r"^返回$"),
        re.compile(r"^退出$"),
        re.compile(r"^离开$"),
    ]
    try:
        frames = list(page.frames)
    except Exception:
        return False
    for fr in frames:
        for role_sel in ("button", '[role="button"]'):
            for pat in patterns:
                try:
                    loc = fr.locator(role_sel).filter(has_text=pat)
                    if await loc.count() == 0:
                        continue
                    btn = loc.first
                    if not await btn.is_visible(timeout=600):
                        continue
                    await btn.click(timeout=2500)
                    log(
                        f"  [round-wait] 已点击回大厅类按钮（{role_sel} {pat.pattern!r}）"
                    )
                    await page.wait_for_timeout(500)
                    return True
                except Exception:
                    continue
    return False


async def _wait_for_natural_round_end(
    page: Any,
    *,
    log: Callable[[str], None],
    min_wait_sec: float,
    max_wait_sec: float,
    poll_sec: float = 2.8,
    stable_polls_needed: int = 6,
    min_blob_for_stable: int = 400,
    game_id: str = "",
    verbose: bool = False,
    strict_natural_end: bool = True,
) -> tuple[bool, str, float]:
    """
    在已进 game-frame 后等待：用户不操作，依赖对局自行结束。
    聚合主文档/子 frame 正文 + 按钮与 aria-live（含 shadow），并间歇尝试点掉结算遮罩；必要时以「URL 离开 game-frame」兜底。
    game_id 用于追加各游戏终局关键词（Color Blitz / Royal Pusoy 等）。
    若前端写入 window 终局埋点（见 ``_probe_window_round_status``），优先识别。
    返回 (是否认为已终局, 说明, 墙钟秒)。

    **strict_natural_end（默认 True）**：禁止在对局中途误点「Exit/回大厅」；禁止 ~90s 量级的
    「短正文连续兜底」「长稳哈希兜底」等易误判路径（否则金币尚未结算就会被拉回大厅）。
    放宽请使用 ``--lenient-round-end`` 或环境变量 ``K11_P0_LENIENT_ROUND_END=1``。
    """
    end_re = _compile_round_end_re(game_id)
    mbs = int(min_blob_for_stable)
    _gid_l = (game_id or "").strip().lower()
    if _gid_l == "color_blitz":
        mbs = min(mbs, 340)
    elif _gid_l == "royal_pusoy":
        mbs = min(mbs, 360)

    t0 = time.monotonic()
    shell_mark = "game-frame"
    entry_url = ""
    try:
        entry_url = (page.url or "").lower()
    except Exception:
        pass
    await page.wait_for_timeout(int(max(0.0, min_wait_sec) * 1000))
    deadline = t0 + max(1.0, max_wait_sec)
    last_hash: str | None = None
    stable_run = 0
    last_heartbeat = t0
    last_tap_mono = 0.0
    last_lobby_tap_mono = 0.0
    last_state_log = t0
    short_blob_streak = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        elapsed = now - t0
        poll_effective = min(float(poll_sec), 1.15) if elapsed > min_wait_sec + 5 else float(
            poll_sec
        )

        try:
            cur_url = (page.url or "").lower()
        except Exception:
            cur_url = ""
        if (
            elapsed >= max(12.0, min_wait_sec * 0.5)
            and shell_mark in entry_url
            and shell_mark not in cur_url
        ):
            return (
                True,
                "主文档 URL 已不再含 game-frame（疑对局结束或已中转），进入壳内采币/撤离",
                elapsed,
            )

        if elapsed >= min_wait_sec:
            win_ok, win_detail = await _probe_window_round_status(page)
            if win_ok:
                return (
                    True,
                    f"window 埋点判定终局（方案三）: {win_detail}",
                    time.monotonic() - t0,
                )

        if verbose and (now - last_state_log) >= 25.0:
            try:
                tu = (page.url or "")[:220]
                tt = (await page.title() or "")[:140]
                log(f"  [round-wait][state] url={tu!r} title={tt!r}")
            except Exception:
                pass
            last_state_log = now

        blob = await _build_round_end_probe_blob(page)
        settlement_like = bool(end_re.search(blob))

        if elapsed >= min_wait_sec and (now - last_tap_mono) >= 4.0:
            if await _try_tap_settlement_dismiss(page, log):
                last_tap_mono = now
                continue

        # 严格模式：仅当 probe 已出现终局/结算类文案时，才尝试点「回大厅」，避免对局中途点到 Exit/Home
        if elapsed >= min_wait_sec and (now - last_lobby_tap_mono) >= 7.0:
            allow_lobby_tap = (not strict_natural_end) or settlement_like
            if allow_lobby_tap and await _try_tap_back_to_lobby(
                page, log, elapsed=elapsed, min_wait_sec=min_wait_sec
            ):
                last_lobby_tap_mono = now
                last_tap_mono = now
                continue

        if settlement_like:
            elapsed = time.monotonic() - t0
            preview = blob.replace("\n", " ")[:96]
            gid_note = f"（game_id={game_id!r}）" if game_id else ""
            return (
                True,
                f"终局/结算类文案命中{gid_note}（probe 摘要: {preview!r}…）",
                elapsed,
            )
        # 部分游戏整局几乎无可见 innerText（纯 Canvas）；宽松模式下才启用短正文/长稳兜底（易在 ~90s 误判）
        if not strict_natural_end:
            if elapsed >= min_wait_sec + max(75.0, min_wait_sec * 0.55):
                if len(blob) < 180:
                    short_blob_streak += 1
                else:
                    short_blob_streak = 0
                if short_blob_streak >= 8:
                    return (
                        True,
                        "壳内聚合正文持续很短（≥8 轮），按 Canvas/轻 DOM 局结束兜底，便于采币撤离",
                        time.monotonic() - t0,
                    )
            else:
                short_blob_streak = 0
            digest = hashlib.md5(blob.encode("utf-8", errors="ignore")).hexdigest()
            if len(blob) >= mbs and digest == last_hash:
                stable_run += 1
            else:
                stable_run = 0
            last_hash = digest
            if stable_run >= stable_polls_needed:
                elapsed = time.monotonic() - t0
                return (
                    True,
                    f"正文长稳兜底（≥{mbs} 字且连续 {stable_polls_needed} 次 poll 哈希相同，偏 Canvas 局）",
                    elapsed,
                )
        else:
            short_blob_streak = 0
        if now - last_heartbeat >= 30.0:
            log(
                f"  [round-wait] 仍在等待对局自然结束… {now - t0:.0f}s / {max_wait_sec:.0f}s"
            )
            last_heartbeat = now
        await page.wait_for_timeout(int(max(0.25, poll_effective) * 1000))
    elapsed = time.monotonic() - t0
    hint = (
        "严格模式仅认 window 埋点、终局/结算文案、主文档离开 game-frame；"
        "可调大 --round-max-wait-sec，纯 Canvas 可试 --lenient-round-end，或前端暴露 gameStatus。"
        if strict_natural_end
        else f"{max_wait_sec:.0f}s 内未匹配终局/UI/window 埋点且无 Canvas 长稳兜底"
        "（可调大 K11_P0_ROUND_MAX_WAIT_SEC；或前端暴露 window.gameStatus；或改用接口余额旁路）"
    )
    return (False, hint, elapsed)


def _finalize_per_game_verdict(row: dict[str, Any]) -> None:
    """
    单款：进壳 + 等到终局启发式 + 回大厅 → PASS。
    """
    ok = (
        bool(row.get("shell_game_frame"))
        and bool(row.get("round_natural_end"))
        and bool(row.get("back_to_lobby"))
    )
    row["verdict"] = "PASS" if ok else "FAIL"
    bits: list[str] = []
    bits.append("主文档已含 game-frame" if row.get("shell_game_frame") else "主文档未进 game-frame")
    bits.append(
        "已观测对局自然结束/终局启发式"
        if row.get("round_natural_end")
        else "未观测到终局（超时/无文案）"
    )
    bits.append("已回大厅" if row.get("back_to_lobby") else "未确认回大厅")
    row["verdict_detail"] = "；".join(bits)


def _fmt_coin_num(n: float | None) -> str:
    if n is None:
        return "（未能解析为数字）"
    return f"{n:g}"


def _print_intermediate_game_report(
    *,
    gi: int,
    total: int,
    gname: str,
    row: dict[str, Any],
) -> None:
    """
    单款游戏结束后立即输出：金币采样摘要 + 总评 / 金币行（不依赖 --quiet，便于跟屏）。
    全部跑完后仍会再打印一遍 `_print_k11_p0_report` 汇总。
    """
    idx = gi + 1
    wb = row.get("wallet_lobby_before_num")
    ws = row.get("wallet_in_shell_num")
    wa = row.get("wallet_number_after_lobby")
    if not isinstance(wb, (int, float)):
        wb = None
    if not isinstance(ws, (int, float)):
        ws = None
    if not isinstance(wa, (int, float)):
        wa = None

    wbn = str(row.get("wallet_lobby_before_parse_note") or "").strip()
    wsn = str(row.get("wallet_in_shell_parse_note") or "").strip()
    wan = str(row.get("wallet_number_after_parse_note") or "").strip()
    dlt = row.get("coin_delta_lobby_after_game")
    dnote = str(row.get("coin_delta_note") or "").strip()

    v_tot = str(row.get("verdict") or "FAIL")
    v_coin = str(row.get("coin_line_verdict") or "SKIP")
    vd = str(row.get("verdict_detail") or "").strip()

    print("", flush=True)
    print(
        f"════════ 本局小结 [{idx}/{total}] {gname} ════════",
        flush=True,
    )
    print(
        f"  [金币] 开局前（大厅粗采样）: {_fmt_coin_num(wb)}"
        + (f"  ← {wbn}" if wbn else ""),
        flush=True,
    )
    if row.get("shell_game_frame") and not row.get("click_error"):
        print(
            f"  [金币] 终局后（壳内快照）: {_fmt_coin_num(ws)}"
            + (f"  ← {wsn}" if wsn else ""),
            flush=True,
        )
    else:
        print("  [金币] 终局后（壳内快照）: —（未进壳或点击异常，未采）", flush=True)
    print(
        f"  [金币] 回大厅后（大厅粗采样）: {_fmt_coin_num(wa)}"
        + (f"  ← {wan}" if wan else ""),
        flush=True,
    )
    if isinstance(dlt, (int, float)):
        print(f"  [金币] 本局 Δ（用于报告）: {dlt:g}", flush=True)
        if dnote:
            print(f"       说明: {dnote}", flush=True)
    else:
        print("  [金币] 本局 Δ: —（无法计算）", flush=True)

    mark_g = "✓" if v_coin == "PASS" else ("✗" if v_coin == "FAIL" else "○")
    mark_t = "✓" if v_tot == "PASS" else "✗"
    print(
        f"  [结果] 金币同步行: {mark_g} {v_coin}    |    本局总评（进壳+终局+回厅）: {mark_t} {v_tot}",
        flush=True,
    )
    if vd:
        print(f"         总评明细: {vd}", flush=True)
    be = str(row.get("back_note") or "").strip()
    if be and not row.get("back_to_lobby"):
        print(f"         回厅备注: {be[:200]}", flush=True)
    print("════════" * 4, flush=True)
    print("", flush=True)


def _coin_line_verdict(
    before_num: float | None,
    after_num: float | None,
    shell_num: float | None,
    *,
    round_ok: bool,
    lobby_ok: bool,
) -> str:
    """
    单局金币行：完局且回厅后，优先「局前大厅 vs 局后大厅」；若回厅后 DOM 仍解析不到余额，
    但壳内终局快照有钱包数字，则 PASS（Δ=壳内−局前），避免误 SKIP。
    """
    if not (round_ok and lobby_ok):
        return "SKIP"
    if before_num is not None and after_num is not None:
        return "PASS"
    if before_num is not None and shell_num is not None:
        return "PASS"
    if before_num is None and after_num is None and shell_num is None:
        return "SKIP"
    return "FAIL"


def _print_k11_p0_report(
    *,
    per_game: list[dict[str, Any]],
    p0_key_card: bool,
    p0_all_games: bool,
    coin_verdict: str,
    coin_detail: str,
    overall: bool,
) -> None:
    """分项打印《K11_平台冒烟测试用例》三列 P0 + 每游戏 verdict。"""
    print("", flush=True)
    print("========== K11 平台冒烟 P0（本脚本分项结果）==========", flush=True)
    print("【每款游戏·独立 verdict】", flush=True)
    for x in per_game:
        gid = str(x.get("game_id") or "")
        v = str(x.get("verdict") or "FAIL")
        vd = (x.get("verdict_detail") or "").strip()
        ce = str(x.get("coin_line_verdict") or "SKIP")
        rw = x.get("round_wait_wall_sec")
        rw_s = f"{float(rw):.0f}s" if isinstance(rw, (int, float)) else "—"
        mark = "[PASS]" if v == "PASS" else "[FAIL]"
        line = f"  {mark}  {gid:20}  总评:{v}  金币行:{ce}  等终局:{rw_s}"
        if vd:
            line += f"  | {vd}"
        print(line, flush=True)
    print("", flush=True)
    print("【文档测评项 · 与 K11_平台冒烟测试用例.md 对应】", flush=True)
    kc = "PASS" if p0_key_card else "FAIL"
    print(
        f"  1) 关键卡片点击 — 至少 1 个核心游戏卡片可进入下一层/流程  : {kc}",
        flush=True,
    )
    if not p0_key_card:
        print("      （本项要求：任一款进主文档 game-frame）", flush=True)
    ag = "PASS" if p0_all_games else "FAIL"
    print(
        f"  2) 各游戏正常运行（进壳+等终局+回厅）                    : {ag}",
        flush=True,
    )
    if not p0_all_games:
        print("      （本项要求：每款进壳、终局、回厅均 PASS）", flush=True)
    co = str(coin_verdict).upper()
    print(
        f"  3) 游戏金币同步（大厅余额粗采样；无稳定数字时 SKIP）    : {co}",
        flush=True,
    )
    if coin_detail:
        print(f"      说明: {coin_detail[:220]}{'…' if len(coin_detail) > 220 else ''}", flush=True)
    print("", flush=True)
    tot = "PASS" if overall else "FAIL"
    print(f"总评（三项均满足规则且金币非 FAIL 时为 PASS）: {tot}", flush=True)
    print("=======================================================", flush=True)


def _mcp_imports():
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        _diagnose_and_click_kalaroko_game_entry,
        _evaluate_metrics_with_retry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
        _tactical_retreat_to_platform_home,
    )

    return (
        _diagnose_and_click_kalaroko_game_entry,
        _evaluate_metrics_with_retry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
        _tactical_retreat_to_platform_home,
    )


async def _run_one_game_e2e_like(
    page: Any,
    *,
    scen: dict[str, Any],
    home: str,
    log: Callable[[str], None],
    shell_phase_timeout_ms: int | None = None,
) -> dict[str, Any]:
    """
    单款游戏：与 ``execute_playwright_perf_test`` 内非首页场景同一套路（无独立 browser launch）。
    """
    (
        _diagnose_and_click_kalaroko_game_entry,
        _evaluate_metrics_with_retry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
        _tactical_retreat_to_platform_home,
    ) = _mcp_imports()

    scen = dict(scen)
    scen["start_url"] = home
    name = str(scen.get("name") or "game")
    click_sel = str(scen.get("click_selector") or "").strip()
    if not click_sel:
        return {
            "game_id": name,
            "ok": False,
            "error": "缺少 click_selector",
        }

    entry_wait = str(scen.get("entry_wait_until") or "load")
    click_to = int(scen.get("click_timeout_ms") or 10_000)
    deep_ms = int(scen.get("timeout_ms") or 90_000)
    if shell_phase_timeout_ms is not None:
        timeout_ms = max(60_000, int(shell_phase_timeout_ms), deep_ms)
    else:
        timeout_ms = max(60_000, deep_ms)

    try:
        await _goto_resilient(page, home, entry_wait, timeout_ms)
    except Exception as e:
        return {"game_id": name, "ok": False, "error": f"goto 大厅: {e}"}

    await _prepare_kalaroko_lobby_after_navigation(page, progress=log)

    ws_times: list[float] = []

    def _on_ws(_ws: Any) -> None:
        try:
            ws_times.append(time.perf_counter())
        except Exception:
            pass

    page.on("websocket", _on_ws)
    ws_times.clear()
    t0 = time.perf_counter()
    err_click = None
    try:
        await _diagnose_and_click_kalaroko_game_entry(
            page,
            click_selector=click_sel,
            click_timeout_ms=click_to,
            scenario_name=name,
            scenario=scen,
            progress=log,
        )
    except Exception as e:
        err_click = str(e)[:500]
    try:
        await _game_deep_wait_after_goto(
            page,
            t0,
            timeout_ms,
            ws_times,
            click_flow=True,
        )
    except Exception as e:
        if not err_click:
            err_click = f"deep_wait: {e}"[:500]

    try:
        page.remove_listener("websocket", _on_ws)
    except Exception:
        pass

    final_url = ""
    try:
        final_url = page.url or ""
    except Exception:
        pass
    shell_ok = "game-frame" in final_url.lower()
    t_end = time.perf_counter()
    real_ms = max(0.0, (t_end - t0) * 1000.0)

    metrics: dict[str, Any] = {}
    if shell_ok and not err_click:
        try:
            m = await _evaluate_metrics_with_retry(page)
            if isinstance(m, dict):
                metrics = {
                    "ttfb_ms": m.get("ttfb_ms"),
                    "fcp_ms": m.get("fcp_ms"),
                }
        except Exception as ex:
            metrics = {"_evaluate_error": str(ex)[:200]}

    entered = bool(shell_ok and not err_click)
    out: dict[str, Any] = {
        "game_id": name,
        "ok": entered,
        "load_status": "success" if entered else "failed",
        "shell_game_frame": shell_ok,
        "final_url": final_url[:400],
        "real_engine_load_ms": round(real_ms, 1),
        "click_error": err_click,
        "metrics": metrics,
    }
    if not shell_ok and not err_click:
        out["error"] = "主文档 URL 未含 game-frame（与 MCP require_game_frame_url 一致）"
    return out


async def _back_to_lobby(
    page: Any,
    *,
    p0: Any,
    target_url: str,
    home: str,
    scen: dict[str, Any],
    log: Callable[[str], None],
) -> tuple[bool, str]:
    _tactical_retreat_to_platform_home = _mcp_imports()[5]
    ok_x, x_note = await p0._p0_exit_game_via_kk_then_exit(page)
    if ok_x:
        t_end = time.monotonic() + 22.0
        while time.monotonic() < t_end:
            if await p0._p0_lobby_seems_visible(page):
                return True, f"KK/Exit: {x_note}"
            await page.wait_for_timeout(450)
    log("  [lobby] KK/Exit 未确认大厅，尝试 MCP 战术撤离…")
    try:
        await _tactical_retreat_to_platform_home(page, scen, progress=log)
    except Exception as e:
        return False, f"撤离失败: {e}"
    try:
        await p0._ensure_on_home_feed(page, target_url, log)  # type: ignore[attr-defined]
    except Exception:
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=50_000)
        except Exception:
            pass
    if await p0._p0_lobby_seems_visible(page):
        return True, "战术撤离后可见大厅"
    return False, "未确认回大厅（请人工看屏）"


async def _async_main(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    p0 = _load_p0_smoke_module()

    cdp = p0._kalaroko_cdp(args.cdp_http or None)
    target_url = (args.target_url or DEFAULT_TARGET).strip()
    host = p0._host_from_url(target_url)
    home = p0._home_feed_url(target_url)
    all_names = _game_names_from_mcp_defaults()
    to_run = [args.game] if args.single else all_names
    for g in to_run:
        if g not in all_names:
            print(f"[失败] 未知 --game {g!r}", file=sys.stderr)
            return 2

    def log(msg: str) -> None:
        if args.verbose or not args.quiet:
            print(msg, flush=True)

    def progress(msg: str) -> None:
        log(f"  [mcp] {msg}")

    rmin = (
        float(args.round_min_wait_sec)
        if args.round_min_wait_sec is not None
        else _env_float("K11_P0_ROUND_MIN_WAIT_SEC", 20.0)
    )
    rmax = (
        float(args.round_max_wait_sec)
        if args.round_max_wait_sec is not None
        else _env_float("K11_P0_ROUND_MAX_WAIT_SEC", 600.0)
    )
    rmax = max(60.0, rmax)
    lenient_round = bool(getattr(args, "lenient_round_end", False)) or _env_truthy(
        "K11_P0_LENIENT_ROUND_END", False
    )
    strict_natural = not lenient_round
    # 进壳阶段与单局最长等待对齐，避免 MCP 默认 90s deep_wait 与「等满一局」脱节
    shell_phase_timeout_ms = max(120_000, int(rmax * 1000))
    log("———————— K11 P0 · 关键卡片 / 多游戏 / 金币（进壳 + 等终局 + 金币）————————")
    log(f"CDP：{cdp}  站点：{home}  本轮游戏：{', '.join(to_run)}")
    log(
        f"单局等待终局：min={rmin:.0f}s max={rmax:.0f}s poll={args.round_poll_sec:.1f}s"
        f"  自然终局严格模式={'开' if strict_natural else '关（--lenient-round-end）'}"
    )
    log("")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        navigate_if_no_tab = not getattr(args, "require_existing_tab", False)
        page, pick_err = await p0._acquire_cdp_target_page(
            browser,
            host=host,
            target_url=target_url,
            navigate_if_no_tab=navigate_if_no_tab,
            log=log,
        )
        if page is None:
            print(f"[失败] {pick_err or '无法获取目标页签'}", file=sys.stderr)
            return 2

        ok_env, env_detail = await p0._ensure_target_page(
            page,
            target_url,
            log=log,
            navigate_if_no_tab=navigate_if_no_tab,
            host=host,
        )
        if not ok_env:
            print(f"[失败] {env_detail}", file=sys.stderr)
            return 2

        use_mobile_vp = not bool(getattr(args, "no_mobile_viewport", False))
        vp_w, vp_h = DEFAULT_VIEWPORT_W, DEFAULT_VIEWPORT_H
        parsed_vp = _parse_viewport_wh((getattr(args, "viewport", None) or "").strip())
        if parsed_vp:
            vp_w, vp_h = parsed_vp
        if use_mobile_vp:
            await _apply_mobile_viewport(page, width=vp_w, height=vp_h, log=log)
        else:
            log("已跳过手机视口（--no-mobile-viewport），沿用当前浏览器视口")

        log("准备：锚定大厅首页…")
        await p0._ensure_on_home_feed(page, target_url, log)
        try:
            if (page.url or "").rstrip("/") != home.rstrip("/"):
                await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(500)
        except Exception as e:
            log(f"  [warn] {p0._brief_exc(e)}")

        (
            _unused_click,
            _unused_eval,
            _unused_deep,
            _unused_goto,
            _prepare_kalaroko_lobby,
            _unused_tac,
        ) = _mcp_imports()
        await _prepare_kalaroko_lobby(page, progress=progress)

        log("基线：大厅钱包/余额粗采样（金币同步用）…")
        baseline_wallet = await _snapshot_lobby_wallet(page)
        b_num, b_note = _first_number_from_hints(baseline_wallet)
        print(
            f"[金币][整轮基线] 大厅粗采样: {_fmt_coin_num(b_num)}"
            + (f"  ← {b_note}" if b_note else ""),
            flush=True,
        )
        print("", flush=True)

        per_game: list[dict[str, Any]] = []
        any_shell = False
        for gi, gname in enumerate(to_run):
            log(f"—— 游戏 {gi + 1}/{len(to_run)}：{gname} ——")
            if gi > 0:
                log("  [between-games] 重新锚定大厅，避免上一局 iframe/shadow 状态影响本局终局探测…")
                try:
                    await p0._ensure_on_home_feed(page, target_url, log)
                    if (page.url or "").rstrip("/") != home.rstrip("/"):
                        await page.goto(
                            home, wait_until="domcontentloaded", timeout=60_000
                        )
                    await _prepare_kalaroko_lobby(page, progress=progress)
                    await page.wait_for_timeout(700)
                except Exception as e:
                    log(f"  [warn] between-games: {p0._brief_exc(e)}")
            scen = _scenario_copy_for_game(gname)
            scen["start_url"] = home

            w_lobby_before = await _snapshot_lobby_wallet(page)
            wb_num, wb_note = _first_number_from_hints(w_lobby_before)
            print(
                f"[金币][开局 {gi + 1}/{len(to_run)} · {gname}] 局前大厅粗采样: "
                f"{_fmt_coin_num(wb_num)}"
                + (f"  ← {wb_note}" if wb_note else ""),
                flush=True,
            )

            row = await _run_one_game_e2e_like(
                page,
                scen=scen,
                home=home,
                log=progress,
                shell_phase_timeout_ms=shell_phase_timeout_ms,
            )
            row["wallet_lobby_before"] = w_lobby_before
            row["wallet_lobby_before_num"] = wb_num
            row["wallet_lobby_before_parse_note"] = wb_note

            if row.get("shell_game_frame") and not row.get("click_error"):
                any_shell = True
                ok_end, end_detail, wall_sec = await _wait_for_natural_round_end(
                    page,
                    log=progress,
                    min_wait_sec=rmin,
                    max_wait_sec=rmax,
                    poll_sec=float(args.round_poll_sec),
                    stable_polls_needed=int(args.round_stable_polls),
                    min_blob_for_stable=int(args.round_min_blob_stable),
                    game_id=gname,
                    verbose=bool(args.verbose),
                    strict_natural_end=strict_natural,
                )
                row["round_natural_end"] = ok_end
                row["round_end_detail"] = end_detail
                row["round_wait_wall_sec"] = round(wall_sec, 1)
                # 终局判出后略等结算数字/余额条刷进壳内 DOM，再采币（避免刚判终局立刻读仍是旧值）
                try:
                    await page.wait_for_timeout(650)
                except Exception:
                    pass
                try:
                    w_shell = await _snapshot_lobby_wallet(page)
                except Exception as e:
                    w_shell = {"error": str(e)[:200], "hints": []}
                row["wallet_in_shell_after_round"] = w_shell
                ws_num, ws_note = _first_number_from_hints(w_shell)
                row["wallet_in_shell_num"] = ws_num
                row["wallet_in_shell_parse_note"] = ws_note
            else:
                row["round_natural_end"] = False
                row["round_end_detail"] = (
                    "未进 game-frame 或点击阶段异常，跳过等待终局"
                )
                row["round_wait_wall_sec"] = 0.0
                row["wallet_in_shell_after_round"] = None
                row["wallet_in_shell_num"] = None
                row["wallet_in_shell_parse_note"] = ""

            back_ok, back_note = await _back_to_lobby(
                page,
                p0=p0,
                target_url=target_url,
                home=home,
                scen=scen,
                log=progress,
            )

            if back_ok:
                try:
                    await _prepare_kalaroko_lobby(page, progress=progress)
                    await page.wait_for_timeout(400)
                except Exception:
                    pass

            w_after = await _snapshot_lobby_wallet(page)
            a_num, a_note = _first_number_from_hints(w_after)
            if back_ok and a_num is None:
                for _r in range(4):
                    try:
                        await page.wait_for_timeout(550)
                    except Exception:
                        pass
                    w_after = await _snapshot_lobby_wallet(page)
                    a_num, a_note = _first_number_from_hints(w_after)
                    if a_num is not None:
                        break
            row["back_to_lobby"] = back_ok
            row["back_note"] = back_note
            row["wallet_after_lobby"] = w_after
            row["wallet_number_after_lobby"] = a_num
            row["wallet_number_after_parse_note"] = a_note
            # 兼容旧键名
            row["wallet_after"] = w_after
            row["wallet_number_after"] = a_num

            ws_num = row.get("wallet_in_shell_num")
            if not isinstance(ws_num, (int, float)):
                ws_num = None
            clv = _coin_line_verdict(
                wb_num,
                a_num,
                ws_num,
                round_ok=bool(row.get("round_natural_end")),
                lobby_ok=bool(back_ok),
            )
            row.pop("coin_delta_note", None)
            dlt: float | None = None
            if wb_num is not None and a_num is not None:
                dlt = round(float(a_num) - float(wb_num), 4)
            elif wb_num is not None and ws_num is not None:
                dlt = round(float(ws_num) - float(wb_num), 4)
                row["coin_delta_note"] = (
                    "Δ=壳内终局快照−局前大厅（回大厅后仍未解析到余额数字）"
                )
            row["coin_delta_lobby_after_game"] = dlt
            row["coin_line_verdict"] = clv
            _finalize_per_game_verdict(row)
            per_game.append(row)
            _print_intermediate_game_report(
                gi=gi, total=len(to_run), gname=gname, row=row
            )
            if not back_ok and gi < len(to_run) - 1:
                log("[WARN] 未稳回大厅，尝试 goto 首页后继续…")
                try:
                    await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
                    await _prepare_kalaroko_lobby(page, progress=progress)
                except Exception as e:
                    log(f"  [warn] 恢复大厅：{e}")

        log("收尾：整轮结束后再采样一次大厅（金币）…")
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=50_000)
            await _prepare_kalaroko_lobby(page, progress=progress)
        except Exception as e:
            log(f"  [warn] 收尾 goto：{e}")
        final_wallet = await _snapshot_lobby_wallet(page)
        f_num, f_note = _first_number_from_hints(final_wallet)

        # —— 三条 P0 用例结论（与表头对齐） ——
        p0_key_card = any_shell
        p0_all_games = all(
            bool(x.get("shell_game_frame"))
            and bool(x.get("round_natural_end"))
            and bool(x.get("back_to_lobby"))
            for x in per_game
        )

        line_vs = [str(x.get("coin_line_verdict") or "SKIP") for x in per_game]
        if "FAIL" in line_vs:
            coin_verdict = "FAIL"
            coin_detail = "；".join(
                f"{x.get('game_id')!s}:{x.get('coin_line_verdict')!s}" for x in per_game
            )
        elif all(v == "SKIP" for v in line_vs):
            coin_verdict = "SKIP"
            coin_detail = "逐局大厅余额均未能解析为数字，或该局未完局（见 per_game.coin_line_verdict）"
        else:
            coin_verdict = "PASS"
            parts = []
            for x in per_game:
                d = x.get("coin_delta_lobby_after_game")
                gid = x.get("game_id")
                if d is not None:
                    parts.append(f"{gid} Δ={d}")
            whole_delta = None
            if b_num is not None and f_num is not None:
                whole_delta = round(f_num - b_num, 4)
            coin_detail = (
                "逐局（本局后大厅-本局前大厅）：" + ("；".join(parts) if parts else "无 delta")
            )
            if whole_delta is not None:
                coin_detail += f"。整轮基线→收尾大厅 Δ={whole_delta}"

        if b_num is not None and f_num is None and coin_verdict != "FAIL":
            coin_verdict = "FAIL"
            coin_detail = f"基线有数字 {b_num} 但整轮结束仍无法解析大厅余额"

        overall = p0_key_card and p0_all_games and (coin_verdict in ("PASS", "SKIP"))
        if coin_verdict == "FAIL":
            overall = False

        k11_p0 = {
            "p0_key_card_click": {
                "id": "p0_key_card_click",
                "source_row_md": "P0 关键卡片点击",
                "check": "至少 1 个核心游戏卡片点击后可正常进入下一层页面/流程",
                "verdict": "PASS" if p0_key_card else "FAIL",
                "detail": "任一款主文档 URL 已含 game-frame" if p0_key_card else "无任何一款进入 game-frame",
            },
            "p0_all_games_normal": {
                "id": "p0_all_games_normal",
                "source_row_md": "P0 各游戏正常运行",
                "check": "应保证每个游戏正常能够开局和完成一局的游戏内容",
                "automation_scope": (
                    "进 game-frame 后轮询主/子 frame 终局类文案，或长稳兜底；不操作手牌。超时调 "
                    "K11_P0_ROUND_MAX_WAIT_SEC / --round-max-wait-sec。"
                ),
                "verdict": "PASS" if p0_all_games else "FAIL",
                "detail": f"本轮 {len(to_run)} 款均进壳+终局+回厅"
                if p0_all_games
                else "存在进壳/终局/回厅任一项失败",
                "per_game_pass": {str(x.get("game_id") or ""): str(x.get("verdict") or "FAIL") for x in per_game},
            },
            "p0_coin_sync": {
                "id": "p0_coin_sync",
                "source_row_md": "P0 游戏金币同步",
                "check": "保证每个游戏游玩后，金币的扣除和获取没有异常",
                "automation_scope": "每局前后在大厅采余额；能解析则比对 Δ；否则 SKIP/人工。",
                "verdict": coin_verdict,
                "detail": coin_detail,
                "per_game_coin": {
                    str(x.get("game_id") or ""): str(x.get("coin_line_verdict") or "SKIP")
                    for x in per_game
                },
            },
        }

        out: dict[str, Any] = {
            "schema": "k11_p0_key_card_kalaroko_playwright/v10",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "cdp": cdp,
            "viewport": {
                "width": int(vp_w),
                "height": int(vp_h),
                "mobile_layout": bool(use_mobile_vp),
            },
            "target_url": target_url,
            "home_url": home,
            "mode": "single" if args.single else "all_default_games",
            "games": to_run,
            "k11_p0_smoke": k11_p0,
            "mvp_docs": {
                "p0_key_card_click": {
                    "verdict": "PASS" if p0_key_card else "FAIL",
                    "detail": "至少 1 款进入主文档 game-frame" if p0_key_card else "无 game-frame",
                },
                "p0_all_games_runnable": {
                    "verdict": "PASS" if p0_all_games else "FAIL",
                    "detail": f"本轮 {len(to_run)} 款：进壳+终局+回厅全成功"
                    if p0_all_games
                    else "存在进壳/终局/回厅未全部成功",
                },
                "p0_coin_sync_smoke": {
                    "verdict": coin_verdict,
                    "detail": coin_detail,
                },
            },
            "per_game": per_game,
            "wallet_baseline": baseline_wallet,
            "wallet_baseline_number": b_num,
            "wallet_final": final_wallet,
            "wallet_final_number": f_num,
            "verdict": "PASS" if overall else "FAIL",
            "verdict_summary": {
                "per_game": {str(x.get("game_id") or ""): str(x.get("verdict") or "FAIL") for x in per_game},
                "k11_p0": {k: v.get("verdict") for k, v in k11_p0.items()},
                "total": "PASS" if overall else "FAIL",
            },
        }

        if args.json_out:
            outp = Path(args.json_out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(
                json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            log(f"JSON：{outp.resolve()}")

        _print_k11_p0_report(
            per_game=per_game,
            p0_key_card=p0_key_card,
            p0_all_games=p0_all_games,
            coin_verdict=coin_verdict,
            coin_detail=coin_detail,
            overall=overall,
        )
        if overall:
            return 0
        return 1


def main() -> int:
    games = _game_names_from_mcp_defaults()
    ap = argparse.ArgumentParser(
        description="K11 P0：MCP 同源 E2E 子流程（关键卡片+多游戏+金币粗样）"
    )
    ap.add_argument("--target-url", default=DEFAULT_TARGET, help="站点根，用于 host 与大厅根路径")
    ap.add_argument("--cdp-http", default="", help="覆盖 KALAROKO_CDP_ENDPOINT")
    ap.add_argument(
        "--game",
        default="tongits_king",
        choices=games,
        help="与 KALAROKO_DEFAULT_SCENARIOS 一致；配合 --single 只跑一款",
    )
    ap.add_argument(
        "--single",
        action="store_true",
        help="只跑 --game 指定的一款（调试用）；默认 false=顺序跑满三款",
    )
    ap.add_argument(
        "--require-existing-tab",
        action="store_true",
        help="必须已有含目标域页签；默认允许自动 goto",
    )
    ap.add_argument(
        "--viewport",
        default=_default_viewport_str(),
        help=(
            f"手机测试视口 WxH，例如 459x851（默认与 Chrome 设备条/响应式常见直板尺寸一致；"
            f"可设环境 K11_P0_VIEWPORT）"
        ),
    )
    ap.add_argument(
        "--no-mobile-viewport",
        action="store_true",
        help="不调用 set_viewport_size，沿用当前浏览器/窗口的视口（桌面布局）",
    )
    ap.add_argument(
        "--round-min-wait-sec",
        type=float,
        default=None,
        help="进壳后最短等待再轮询终局（默认 env K11_P0_ROUND_MIN_WAIT_SEC 或 20）",
    )
    ap.add_argument(
        "--round-max-wait-sec",
        type=float,
        default=None,
        help="单局等待对局自然结束的最长时间秒（默认 env K11_P0_ROUND_MAX_WAIT_SEC 或 600）",
    )
    ap.add_argument(
        "--round-poll-sec",
        type=float,
        default=1.25,
        help="终局轮询间隔秒（进壳较久后会自动收紧到约 1.15s；黑盒方案可设 5～10 降频）",
    )
    ap.add_argument(
        "--round-stable-polls",
        type=int,
        default=6,
        help="正文哈希连续相同多少次视为 Canvas 长稳兜底（防误报可加大）",
    )
    ap.add_argument(
        "--round-min-blob-stable",
        type=int,
        default=600,
        help="长稳兜底要求的最小聚合正文字数",
    )
    ap.add_argument(
        "--lenient-round-end",
        action="store_true",
        help="恢复旧版「短正文/长稳/对局中点回大厅」等宽松终局判定（默认严格：等对局自然结束再撤离，利于金币结算）",
    )
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
