#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 冒烟 · Tongits 并行一局 + 协议结算金币记录（与统合 / 开门脚本解耦）。

设计：
- 在**独立浏览器页签**进 Tongits King 并启动 ``main_bot_loop``，不占用主冒烟页签。
- 后台 ``ResultMonitor`` HTTP 服务接收 ``tongits_result_monitor_snippet.js`` 转发的协议结算。
- 收尾时写入一条 Lark 结果行（备注含「游戏金币变化: 通过/失败」与逐人盈亏文案）。
- 任意步骤失败均 **不 raise**，由调用方追加 SKIP/FAIL 行；默认 **不计入** 统合退出码。

环境变量（可选）：
  K11_TONGITS_SMOKE=1           并入统合/开门冒烟时开启（默认 0；独立按钮见 test_k11_tongits_autoplay_smoke.py）
  K11_TONGITS_MONITOR_PORT      监控端口（默认 17889）
  K11_TONGITS_MY_NAME           结算里我方昵称（默认 victor）
  K11_TONGITS_ROUND_WAIT_SEC    收尾额外等待结算秒数（默认 90）
  K11_TONGITS_SKIP_BOT=1        不启动 main_bot_loop（仅协议监控）
  K11_TONGITS_SMOKE_AFFECT_EXIT=1  Tongits FAIL 也拉高统合退出码（默认 0）
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def tongits_smoke_enabled(args: Any | None = None) -> bool:
    if args is not None and bool(getattr(args, "skip_tongits_smoke", False)):
        return False
    return _env_bool("K11_TONGITS_SMOKE", False)


def tongits_affects_exit_code() -> bool:
    return _env_bool("K11_TONGITS_SMOKE_AFFECT_EXIT", False)


def _load_script_module(path: Path, unique_name: str) -> Any | None:
    """动态加载 scripts/*.py；须在 exec_module 前写入 sys.modules（@dataclass 等依赖）。"""
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_coin_smoke_module() -> Any | None:
    return _load_script_module(
        SCRIPTS / "test_k11_game_open_coin_smoke.py",
        "k11_game_open_coin_smoke_embed",
    )


def _load_monitor_module() -> Any | None:
    return _load_script_module(
        SCRIPTS / "tongits_result_monitor.py",
        "tongits_result_monitor_embed",
    )


def _smoke_executed_at_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_tongits_bot_python() -> tuple[Path, str]:
    """自动出牌依赖 YOLO/PyTorch，优先使用项目 OmniParser 虚拟环境。"""
    override = (
        os.environ.get("K11_TONGITS_BOT_PYTHON")
        or os.environ.get("TONGITS_BOT_PYTHON")
        or ""
    ).strip()
    if override:
        return Path(override), "env:K11_TONGITS_BOT_PYTHON/TONGITS_BOT_PYTHON"

    candidates = [
        ROOT / ".venv-omniparser" / "Scripts" / "python.exe",
        ROOT / ".venv-omniparser" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.is_file():
            return p, "auto"
    return Path(sys.executable), "fallback:sys.executable"


def _resolve_tongits_bot_args() -> list[str]:
    raw = (
        os.environ.get("K11_TONGITS_BOT_ARGS")
        or os.environ.get("TONGITS_BOT_ARGS")
        or ""
    ).strip()
    if raw:
        try:
            return shlex.split(raw, posix=(os.name != "nt"))
        except ValueError:
            return raw.split()
    mode = (
        os.environ.get("K11_TONGITS_SCOUT_MODE")
        or os.environ.get("TONGITS_BUTTON_SCOUT_MODE")
        or "qwen_full"
    ).strip().lower().replace("-", "_")
    mode_args = {
        "qwen_full": ["--qwen-full"],
        "qwen": ["--qwen-full"],
        "yolo_full": ["--full-yolo"],
        "yolo": ["--full-yolo"],
        "hybrid": ["--hybrid"],
        "florence_local": ["--florence-local"],
        "florence": ["--florence-local"],
    }.get(mode, ["--qwen-full"])
    return [*mode_args, "--auto-play", "--auto-play-live"]


def _load_key_values_from_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip().lstrip("export ").strip()
        val = v.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def _hydrate_bot_secret_env(env: dict[str, str]) -> list[str]:
    """给 bot 子进程补齐 VLM key；不记录密钥值。"""
    keys = {
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_API_KEY_CN",
        "DASHSCOPE_API_KEY_SEA",
        "DASHSCOPE_API_BASE",
        "DASHSCOPE_API_BASE_CN",
        "DASHSCOPE_API_BASE_SEA",
        "QWEN_API_KEY",
        "QWEN_AI_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "TONGITS_VLM_PROVIDER",
        "TONGITS_VLM_MODEL",
        "JACHIN_ACTIVE_REGION",
    }
    candidates = [
        Path.home() / ".jachin" / ".env",
        ROOT / "clients" / "desktop" / ".env",
        ROOT / ".env",
    ]
    loaded: list[str] = []
    for path in candidates:
        vals = _load_key_values_from_env_file(path)
        if not vals:
            continue
        touched = False
        for key in keys:
            val = vals.get(key)
            if val:
                env[key] = val
                touched = True
        if touched:
            loaded.append(str(path))
    return loaded


def _decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _identity_from_tongits_url(url: str) -> tuple[str, str]:
    """从 game-frame/frameUrl/token 中提取当前玩家 nickname/user_id。"""
    urls = [str(url or "")]
    try:
        qs = parse_qs(urlparse(urls[0]).query)
        frame_url = (qs.get("frameUrl") or qs.get("frame_url") or [""])[0]
        if frame_url:
            urls.append(unquote(frame_url))
    except Exception:
        pass

    best_name = ""
    best_uid = ""
    for u in urls:
        try:
            qs = parse_qs(urlparse(u).query)
        except Exception:
            qs = {}
        direct_uid = (qs.get("user_id") or qs.get("userId") or [""])[0]
        token = (qs.get("token") or [""])[0]
        payload = _decode_jwt_payload_unverified(token)
        name = str(
            payload.get("nickname")
            or payload.get("nickName")
            or payload.get("name")
            or payload.get("userName")
            or ""
        ).strip()
        uid = str(payload.get("user_id") or payload.get("userId") or direct_uid or "").strip()
        if name and not best_name:
            best_name = name
        if uid and not best_uid:
            best_uid = uid
    return best_name, best_uid


async def _detect_tongits_page_identity(page: Any) -> tuple[str, str]:
    urls: list[str] = []
    try:
        urls.append(str(page.url or ""))
    except Exception:
        pass
    try:
        urls.extend(str(getattr(fr, "url", "") or "") for fr in page.frames)
    except Exception:
        pass
    for u in urls:
        name, uid = _identity_from_tongits_url(u)
        if name or uid:
            return name, uid
    return "", ""


async def tongits_safe_select_coins_join(
    page: Any,
    *,
    log: Callable[[str], None],
    mcp_mod: Any,
    budget_sec: float = 18.0,
) -> tuple[bool, str]:
    """
    在 Select Coins / One Round 弹层内点筹码 + Join。
    禁止「饱和打击」式全页 mouse 连点（易误触 YouTube subscribers 转化层）。
    """
    try:
        mcp_mod.register_kalaroko_popup_guardian(page.context)
    except Exception:
        pass

    def progress(msg: str) -> None:
        log(f"  [tongits·join] {msg}")

    await mcp_mod._dismiss_kalaroko_subscribers_modal(  # type: ignore[attr-defined]
        page, progress=progress, click_timeout_ms=4000
    )
    try:
        await mcp_mod._dismiss_kalaroko_blocking_promos(  # type: ignore[attr-defined]
            page, progress=progress, click_timeout_ms=3000, lobby_fast=True
        )
    except Exception:
        pass

    coin_title = re.compile(r"Select\s+Coins", re.I)
    one_round = re.compile(r"One\s+Round", re.I)
    join_pat = re.compile(r"^\s*Join\s*$", re.I)
    chip_pat = re.compile(r"^(100|200|500|1000|2000|5000|10000|20000|50000)$")

    deadline = time.monotonic() + max(3.0, float(budget_sec))
    last_note = "等待 Select Coins 弹层…"

    while time.monotonic() < deadline:
        try:
            await mcp_mod._dismiss_kalaroko_subscribers_modal(  # type: ignore[attr-defined]
                page, progress=None, click_timeout_ms=1500
            )
        except Exception:
            pass

        try:
            frames = [page.main_frame] + [
                f for f in page.frames if f != page.main_frame
            ]
        except Exception:
            frames = [page.main_frame]

        for fr in frames:
            try:
                has_coin = await fr.get_by_text(coin_title).count()
                has_round = await fr.get_by_text(one_round).count()
                if has_coin < 1 and has_round < 1:
                    continue

                fr_label = (fr.url or "main")[:72]
                log(f"  [tongits·join] 发现选币弹层 frame={fr_label}")

                chips = fr.get_by_text(chip_pat)
                chip_cnt = await chips.count()
                for i in range(min(chip_cnt, 8)):
                    chip = chips.nth(i)
                    try:
                        if await chip.is_visible(timeout=180):
                            await chip.click(timeout=900, force=True)
                            log("  [tongits·join] 已选筹码")
                            await page.wait_for_timeout(220)
                            break
                    except Exception:
                        continue

                jn = fr.get_by_role("button", name=join_pat)
                if await jn.count() > 0:
                    await jn.last.click(timeout=5000, force=True)
                    log("  [tongits·join] 已点击 Join（选币弹层内）")
                    await page.wait_for_timeout(600)
                    return True, "Select Coins 弹层 Join 已点击"

                jt = fr.get_by_text(join_pat)
                if await jt.count() > 0:
                    await jt.last.click(timeout=5000, force=True)
                    log("  [tongits·join] 已点击 Join 文本（选币弹层内）")
                    await page.wait_for_timeout(600)
                    return True, "Select Coins 弹层 Join 文本已点击"
            except Exception as exc:
                last_note = f"选币弹层尝试异常: {type(exc).__name__}"
                continue

        await page.wait_for_timeout(450)

    return False, last_note


_TONGITS_SUPPRESS_YOUTUBE_PROMOS_JS = r"""() => {
  let touched = 0;
  const bad = /youtube|youtu\.be|larogoph|subscribe|subscriber/i;

  for (const a of Array.from(document.querySelectorAll("a[href]"))) {
    const href = String(a.getAttribute("href") || "");
    if (!bad.test(href)) continue;
    a.removeAttribute("href");
    a.removeAttribute("target");
    a.removeAttribute("onclick");
    a.style.setProperty("pointer-events", "none", "important");
    touched += 1;
  }

  const nodes = Array.from(document.querySelectorAll("body *"));
  for (const el of nodes.slice(0, 1800)) {
    const cls = String(el.className || "").toLowerCase();
    const role = String(el.getAttribute("role") || "").toLowerCase();
    const id = String(el.id || "").toLowerCase();
    if (
      !cls.includes("modal") &&
      !cls.includes("overlay") &&
      !cls.includes("popup") &&
      !cls.includes("subscribe") &&
      !id.includes("subscribe") &&
      role !== "dialog"
    ) {
      continue;
    }
    const text = String(el.innerText || el.textContent || "").slice(0, 3000);
    let hasBadLink = false;
    try {
      hasBadLink = !!el.querySelector("a[href*='youtube'],a[href*='youtu.be']");
    } catch (e) {}
    if (!bad.test(text) && !hasBadLink) continue;
    el.style.setProperty("display", "none", "important");
    el.style.setProperty("visibility", "hidden", "important");
    el.style.setProperty("pointer-events", "none", "important");
    el.setAttribute("data-tongits-youtube-suppressed", "1");
    touched += 1;
  }
  return touched;
}"""


_TONGITS_CLICK_ENTRY_CANDIDATE_JS = r"""(nth) => {
  const title = /tongits\s*king/i;
  const bad = /youtube|youtu\.be|larogoph|subscribe|subscriber/i;
  const vw = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
  const vh = Math.max(document.documentElement.clientHeight || 0, window.innerHeight || 0);

  function norm(s) {
    return String(s || "").replace(/\s+/g, " ").trim();
  }

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || st.pointerEvents === "none")
      return false;
    const r = el.getBoundingClientRect();
    return r.width >= 40 && r.height >= 24 && r.bottom >= 0 && r.right >= 0 &&
      r.top <= vh + 80 && r.left <= vw + 80;
  }

  function badNode(el) {
    if (!el) return true;
    const text = norm(el.innerText || el.textContent).slice(0, 2500);
    if (bad.test(text)) return true;
    try {
      if (el.querySelector("a[href*='youtube'],a[href*='youtu.be']")) return true;
    } catch (e) {}
    return false;
  }

  function cardRoot(el) {
    let best = el;
    let n = el;
    for (let depth = 0; n && n !== document.body && depth < 8; depth++, n = n.parentElement) {
      const text = norm(n.innerText || n.textContent);
      if (!title.test(text) || badNode(n)) continue;
      const r = n.getBoundingClientRect();
      if (r.width >= 90 && r.height >= 55 && r.width <= Math.max(900, vw * 1.1)) {
        best = n;
      }
      if (
        n.matches &&
        n.matches("a[href],button,[role='button'],[role='link'],[data-href],[onclick]")
      ) {
        best = n;
        break;
      }
    }
    return best;
  }

  const raw = [];
  const all = Array.from(document.querySelectorAll("a,button,[role='button'],[role='link'],[data-href],[onclick],div,section,article"));
  for (const el of all.slice(0, 2500)) {
    const text = norm(el.innerText || el.textContent);
    if (!title.test(text) || badNode(el)) continue;
    const root = cardRoot(el);
    if (!visible(root) || badNode(root)) continue;
    raw.push(root);
  }

  const seen = new Set();
  const cands = [];
  for (const el of raw) {
    const r = el.getBoundingClientRect();
    const key = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)].join(":");
    if (seen.has(key)) continue;
    seen.add(key);
    const action = el.matches("a[href],button,[role='button'],[role='link'],[data-href],[onclick]")
      ? el
      : (el.querySelector("a[href]:not([href*='youtube']):not([href*='youtu.be']),button,[role='button'],[role='link'],[data-href],[onclick]") || el);
    const ar = action.getBoundingClientRect();
    const href = String(action.getAttribute && action.getAttribute("href") || "");
    if (bad.test(href)) continue;
    const text = norm(el.innerText || el.textContent);
    const shortText = Math.max(0, 1000 - Math.min(text.length, 1000));
    const inView = ar.top >= -20 && ar.left >= -20 && ar.bottom <= vh + 40 && ar.right <= vw + 40;
    const actionable = action !== el || action.matches("a[href],button,[role='button'],[role='link'],[data-href],[onclick]");
    const area = Math.max(1, r.width * r.height);
    const score = (actionable ? 10000 : 0) + (inView ? 3000 : 0) + shortText - Math.abs(area - 26000) / 100;
    cands.push({ el, action, score, text: text.slice(0, 120) });
  }
  cands.sort((a, b) => b.score - a.score);

  const item = cands[nth];
  if (!item) return { ok: false, count: cands.length };

  const target = item.action || item.el;
  try { item.el.scrollIntoView({ block: "center", inline: "center" }); } catch (e) {}
  const r = target.getBoundingClientRect();
  const x = Math.max(4, Math.min(vw - 4, r.left + r.width / 2));
  const y = Math.max(4, Math.min(vh - 4, r.top + r.height / 2));
  const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 };
  try { target.dispatchEvent(new PointerEvent("pointerdown", opts)); } catch (e) {}
  try { target.dispatchEvent(new MouseEvent("mousedown", opts)); } catch (e) {}
  try { target.dispatchEvent(new PointerEvent("pointerup", opts)); } catch (e) {}
  try { target.dispatchEvent(new MouseEvent("mouseup", opts)); } catch (e) {}
  try { target.click(); } catch (e) {
    try { target.dispatchEvent(new MouseEvent("click", opts)); } catch (e2) {}
  }
  return {
    ok: true,
    count: cands.length,
    nth,
    tag: target.tagName,
    href: String(target.getAttribute && target.getAttribute("href") || ""),
    text: item.text
  };
}"""


async def _suppress_tongits_youtube_promos(
    page: Any,
    *,
    log: Callable[[str], None] | None = None,
) -> int:
    total = 0
    try:
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    except Exception:
        frames = [page.main_frame]
    for fr in frames:
        try:
            n = await fr.evaluate(_TONGITS_SUPPRESS_YOUTUBE_PROMOS_JS)
            if isinstance(n, (int, float)):
                total += int(n)
        except Exception:
            continue
    if total and log:
        log(f"  [tongits] 已压制 YouTube/订阅转化层 {total} 处")
    return total


async def _tongits_entry_signal(
    page: Any,
    *,
    coin_mod: Any,
    case: Any,
) -> tuple[bool, str]:
    title_pat = re.compile(r"Select\s+Coins|One\s+Round", re.I)
    try:
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    except Exception:
        frames = [page.main_frame]

    for fr in frames:
        try:
            if await fr.get_by_text(title_pat).count() > 0:
                return True, "select_coins"
        except Exception:
            continue
    try:
        if await coin_mod._shell_or_canvas_present(page, case):  # type: ignore[attr-defined]
            return True, "shell_or_canvas"
    except Exception:
        pass
    try:
        for fr in frames:
            u = str(getattr(fr, "url", "") or "").lower()
            if "game-frame" in u or "gweb." in u or ("heronpro" in u and "game" in u):
                return True, f"frame:{u[:72]}"
    except Exception:
        pass
    return False, ""


async def _click_tongits_entry_verified(
    page: Any,
    *,
    log: Callable[[str], None],
    coin_mod: Any,
    case: Any,
    max_candidates: int = 6,
) -> tuple[bool, str]:
    await _suppress_tongits_youtube_promos(page, log=log)
    ok, why = await _tongits_entry_signal(page, coin_mod=coin_mod, case=case)
    if ok:
        return True, f"already_entered:{why}"

    last_note = "no candidate"
    for idx in range(max(1, int(max_candidates))):
        await _suppress_tongits_youtube_promos(page, log=None)
        try:
            info = await page.evaluate(_TONGITS_CLICK_ENTRY_CANDIDATE_JS, idx)
        except Exception as exc:
            last_note = f"candidate_js_error:{type(exc).__name__}"
            break
        if not isinstance(info, dict) or not info.get("ok"):
            cnt = info.get("count") if isinstance(info, dict) else "?"
            last_note = f"no more candidates (count={cnt})"
            break
        last_note = (
            f"candidate#{idx + 1}/{info.get('count')} "
            f"tag={info.get('tag')} text={str(info.get('text') or '')[:60]!r}"
        )
        log(f"  [tongits] Tongits King 专用入口点击: {last_note}")
        await page.wait_for_timeout(1200)
        await _suppress_tongits_youtube_promos(page, log=None)
        ok, why = await _tongits_entry_signal(page, coin_mod=coin_mod, case=case)
        if ok:
            return True, f"{why}; {last_note}"
    return False, last_note


def format_settlement_remark(settlement: dict[str, Any] | None, *, coin_ok: bool) -> str:
    """生成 Lark 备注：游戏金币变化 + 逐人盈亏（与 settlement.log 风格一致）。"""
    flag = "通过" if coin_ok else "失败"
    head = f"游戏金币变化: {flag}"
    if not settlement:
        return f"{head} | 未收到协议结算"
    my_delta = settlement.get("my_delta")
    outcome = settlement.get("outcome") or "UNKNOWN"
    cn = {"WIN": "胜", "LOSE": "负", "DRAW": "平", "UNKNOWN": "未知"}.get(str(outcome), str(outcome))
    game_no = settlement.get("game_no")
    my_txt = f"{int(my_delta):+d}" if my_delta is not None else "未知"
    opp_parts: list[str] = []
    for p in settlement.get("opponents") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("uid") or p.get("seat") or "?")
        d = p.get("delta")
        if d is None:
            opp_parts.append(name)
        else:
            opp_parts.append(f"{name} {int(d):+d}")
    opp_txt = " | ".join(opp_parts) if opp_parts else "-"
    net = settlement.get("net")
    wins = settlement.get("wins")
    losses = settlement.get("losses")
    draws = settlement.get("draws")
    tail = f"我方 {my_txt} | 对手 {opp_txt}"
    if net is not None:
        tail += f" ｜累计 净{int(net):+d}"
        if wins is not None and losses is not None:
            tail += f" 胜{wins} 负{losses}"
            if draws is not None:
                tail += f" 平{draws}"
    body = f"第{game_no}局 {cn} | {tail}" if game_no else tail
    msg_type = settlement.get("msg_type") or settlement.get("msgType") or "3016"
    return f"{head} | {body}（msgType={msg_type}）"


class TongitsSmokeSession:
    """并行 Tongits 一局：监控服务 + 游戏页签 + 可选 bot 子进程。"""

    def __init__(
        self,
        *,
        my_name: str,
        monitor_port: int,
        out_dir: Path,
        log: Callable[[str], None],
    ) -> None:
        self.my_name = my_name
        self.monitor_port = monitor_port
        self.out_dir = out_dir
        self.log = log
        self.started_at = time.monotonic()
        self.started_at_wall = time.time()
        self.start_error: str | None = None
        self.game_page: Any | None = None
        self._bot_proc: subprocess.Popen[Any] | None = None
        self._monitor: Any | None = None
        self._http_server: ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._settle_event = threading.Event()
        self._last_settlement: dict[str, Any] | None = None
        self._inject_task: asyncio.Task[Any] | None = None
        self._stopped = False
        self._owns_game_page = False
        self._bot_runtime_error: str | None = None
        self._proto_seen_without_record_logged = False
        self._bot_play_started = False
        self._bot_play_started_wall = 0.0
        self._ignored_preplay_settlements = 0

    def _on_settlement(self, data: dict[str, Any]) -> None:
        mt = str(data.get("msg_type") or data.get("msgType") or "")
        if mt and mt not in {"3016", "3017", "3021"}:
            return
        if self._require_bot_activity_before_settlement() and not self._bot_play_started:
            self._ignored_preplay_settlements += 1
            if self._ignored_preplay_settlements <= 3:
                self.log(
                    "  [tongits] 忽略启动前/未打牌结算回调："
                    f"msgType={mt or 'unknown'} my_delta={data.get('my_delta')} "
                    "（尚未看到 bot 回合/出牌活动，疑为 CDP 历史回放）"
                )
            return
        self._last_settlement = dict(data)
        try:
            opp = data.get("opponents") or []
            opp_txt = ", ".join(
                f"{p.get('name') or p.get('uid') or p.get('seat') or '?'}:{p.get('delta')}"
                for p in opp
                if isinstance(p, dict)
            )
            self.log(
                f"  [tongits] 收到 {mt or 'unknown'} 结算: "
                f"outcome={data.get('outcome')} my_delta={data.get('my_delta')} "
                f"game_no={data.get('game_no')} opponents=[{opp_txt}]"
            )
        except Exception:
            self.log(f"  [tongits] 收到 {mt or 'unknown'} 结算: {data}")
        self._settle_event.set()

    @staticmethod
    def _require_bot_activity_before_settlement() -> bool:
        raw = (os.environ.get("K11_TONGITS_REQUIRE_BOT_ACTIVITY_BEFORE_SETTLEMENT") or "1").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def start_monitor_server(self) -> bool:
        mon_mod = _load_monitor_module()
        if mon_mod is None:
            self.start_error = "未找到 tongits_result_monitor.py"
            return False
        try:
            self._monitor = mon_mod.ResultMonitor(  # type: ignore[attr-defined]
                self.out_dir,
                self.my_name,
                discover=False,
                on_settlement=self._on_settlement,
            )
            handler = mon_mod._build_handler(self._monitor)  # type: ignore[attr-defined]
            self._http_server = ThreadingHTTPServer(
                ("127.0.0.1", self.monitor_port),
                handler,
            )
            self._http_thread = threading.Thread(
                target=self._http_server.serve_forever,
                name="k11-tongits-monitor",
                daemon=True,
            )
            self._http_thread.start()
            self.log(
                f"  [tongits] 结算监控已启动 127.0.0.1:{self.monitor_port} "
                f"（my_name={self.my_name!r}）"
            )
            return True
        except Exception as e:
            self.start_error = f"监控启动失败: {type(e).__name__}: {e}"
            self.log(f"  [tongits] {self.start_error}")
            return False

    def _spawn_bot(self) -> None:
        if _env_bool("K11_TONGITS_SKIP_BOT", False):
            self.log("  [tongits] 已跳过 main_bot_loop（K11_TONGITS_SKIP_BOT=1）")
            return
        script = SCRIPTS / "main_bot_loop.py"
        if not script.is_file():
            self.log("  [tongits] 未找到 main_bot_loop.py，跳过自动出牌")
            return
        bot_python, py_source = _resolve_tongits_bot_python()
        if not bot_python.is_file():
            self.log(
                "  [tongits] 自动出牌 Python 不存在，无法启动 YOLO: "
                f"{bot_python}（来源: {py_source}）"
            )
            self.log("  [tongits] 请检查 .venv-omniparser 或设置 K11_TONGITS_BOT_PYTHON")
            return
        env = os.environ.copy()
        loaded_env_files = _hydrate_bot_secret_env(env)
        env["TONGITS_AUTO_PLAY"] = "1"
        env["TONGITS_AUTO_PLAY_DRY_RUN"] = "0"
        env.setdefault("TONGITS_SETTLE_WARMUP_SEC", "15")
        if self.my_name:
            env["TONGITS_MY_NAME"] = str(self.my_name)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        bot_args = _resolve_tongits_bot_args()
        try:
            self._bot_proc = subprocess.Popen(
                [str(bot_python), str(script), *bot_args],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.log(f"  [tongits] main_bot_loop 已后台启动 pid={self._bot_proc.pid}")
            self.log(
                f"  [tongits] bot Python: {bot_python}（来源: {py_source}）"
            )
            self.log(f"  [tongits] bot 参数: {' '.join(bot_args)}")
            self.log(
                "  [tongits] bot 环境: "
                f"TONGITS_AUTO_PLAY={env.get('TONGITS_AUTO_PLAY')} "
                f"TONGITS_AUTO_PLAY_DRY_RUN={env.get('TONGITS_AUTO_PLAY_DRY_RUN')} "
                f"K11_TONGITS_SCOUT_MODE={os.environ.get('K11_TONGITS_SCOUT_MODE') or 'qwen_full'} "
                f"TONGITS_MY_NAME={env.get('TONGITS_MY_NAME')}"
            )
            self.log(
                "  [tongits] VLM key 状态: "
                f"DASHSCOPE={'有' if env.get('DASHSCOPE_API_KEY') or env.get('DASHSCOPE_API_KEY_CN') or env.get('DASHSCOPE_API_KEY_SEA') else '无'} "
                f"QWEN={'有' if env.get('QWEN_API_KEY') or env.get('QWEN_AI_API_KEY') else '无'} "
                f"GEMINI={'有' if env.get('GEMINI_API_KEY') or env.get('GOOGLE_API_KEY') else '无'} "
                f"env_files={len(loaded_env_files)}"
            )
            self._start_bot_log_pump()
        except Exception as e:
            self.log(f"  [tongits] main_bot_loop 启动失败（不阻断冒烟）: {e}")

    def _start_bot_log_pump(self) -> None:
        proc = self._bot_proc
        if proc is None or proc.stdout is None:
            return

        def _pump() -> None:
            try:
                for line in proc.stdout:
                    text = line.rstrip()
                    if not text:
                        continue
                    self.log(f"  [tongits·bot] {text[:1000]}")
                    if self._is_bot_play_activity_line(text) and not self._bot_play_started:
                        self._bot_play_started = True
                        self._bot_play_started_wall = time.time()
                        self.log("  [tongits] 已看到 bot 回合/出牌活动，开始接受本局协议结算")
                    low = text.lower()
                    if (
                        "invalid_api_key" in low
                        or "incorrect api key" in low
                        or "401 unauthorized" in low
                    ):
                        self._bot_runtime_error = (
                            "VLM API key 无效，Qwen 纠错失败；当前会退化为 YOLO，识别/出牌不可靠"
                        )
                        self.log(f"  [tongits] 视觉链路失败: {self._bot_runtime_error}")
            except Exception as exc:
                self.log(f"  [tongits·bot] 日志转发停止: {type(exc).__name__}: {exc}")

        threading.Thread(target=_pump, name="k11-tongits-bot-log", daemon=True).start()

    @staticmethod
    def _is_bot_play_activity_line(text: str) -> bool:
        markers = (
            "回合开始确认",
            "到我的回合了",
            "[出牌]",
            "点击 ",
            "回合完成",
            "turn_dump_completed",
        )
        return any(m in text for m in markers)

    async def _inject_snippet_all_frames(self, page: Any) -> None:
        snippet_path = SCRIPTS / "tongits_result_monitor_snippet.js"
        if not snippet_path.is_file():
            return
        js = snippet_path.read_text(encoding="utf-8")
        port = str(self.monitor_port)
        if "17889" in js and port != "17889":
            js = js.replace("17889", port)
        try:
            await page.context.add_init_script(js)
        except Exception:
            pass
        for fr in page.frames:
            try:
                await fr.evaluate(js)
            except Exception:
                pass

    async def _periodic_inject(self, page: Any) -> None:
        while not self._stopped:
            try:
                await self._inject_snippet_all_frames(page)
            except Exception:
                pass
            await asyncio.sleep(8.0)

    async def enter_tongits_on_page(self, page: Any, *, target_url: str) -> bool:
        """在当前页签全自动：大厅 → 点 Tongits → Join → 等进桌。"""
        coin_mod = _load_coin_smoke_module()
        if coin_mod is None:
            self.start_error = "未找到 test_k11_game_open_coin_smoke.py"
            self.log(f"  [tongits] {self.start_error}")
            return False
        mcp = coin_mod.mcp
        cases = getattr(coin_mod, "GAME_CASES", ())
        case = next((c for c in cases if getattr(c, "game_id", "") == "tongits_king"), None)
        if case is None:
            self.start_error = "coin_smoke 中无 tongits_king"
            self.log(f"  [tongits] {self.start_error}")
            return False
        home = getattr(coin_mod, "TARGET_HOME", target_url) or target_url

        def progress(msg: str) -> None:
            self.log(f"  [tongits·mcp] {msg}")

        try:
            self.game_page = page
            self.log(f"  [tongits] 打开站点 {home}")
            await mcp._goto_resilient(page, home, "domcontentloaded", 30_000)
            await mcp._prepare_kalaroko_lobby_after_navigation(page, progress=progress)
            await _suppress_tongits_youtube_promos(page, log=self.log)
            self.log("  [tongits] 点击 Tongits King 入口…")
            clicked, click_note = await coin_mod._click_entry_with_fallback(  # type: ignore[attr-defined]
                page, case, progress=progress
            )
            await page.wait_for_timeout(800)
            entered, enter_note = await _tongits_entry_signal(
                page, coin_mod=coin_mod, case=case
            )
            if not entered:
                self.log(
                    "  [tongits] 通用入口点击后未看到进场信号，改用 Tongits 专用入口重试"
                )
                retry_ok, retry_note = await _click_tongits_entry_verified(
                    page,
                    log=self.log,
                    coin_mod=coin_mod,
                    case=case,
                    max_candidates=6,
                )
                if retry_ok:
                    clicked = True
                    click_note = retry_note
                    entered = True
                    enter_note = retry_note
                else:
                    if not clicked:
                        raise RuntimeError(click_note or retry_note or "进 Tongits 失败")
                    self.log(
                        f"  [tongits] Tongits 专用入口仍未确认进场: {retry_note}；继续等待选币兜底"
                    )
            else:
                self.log(f"  [tongits] 入口进场信号已确认: {enter_note}")
            await _suppress_tongits_youtube_promos(page, log=self.log)
            self.log("  [tongits] 等待 Select Coins 弹层并安全 Join（避开 YouTube 转化层）…")
            joined, join_note = await tongits_safe_select_coins_join(
                page, log=self.log, mcp_mod=mcp, budget_sec=18.0
            )
            if joined:
                self.log(f"  [tongits] Join 成功: {join_note}")
            else:
                self.log(f"  [tongits] Join 未确认: {join_note}（继续 deep_wait 兜底）")
            self.log("  [tongits] 等待进桌 / 开局…")
            t0 = time.perf_counter()
            ws_times: list[float] = []
            await mcp._game_deep_wait_after_goto(
                page, t0, timeout_ms=90_000, ws_times=ws_times, click_flow=True
            )
            await self._inject_snippet_all_frames(page)
            if self._inject_task is None:
                self._inject_task = asyncio.create_task(self._periodic_inject(page))
            self._spawn_bot()
            self.log("  [tongits] 已进桌，协议监控 + 自动出牌运行中")
            return True
        except Exception as e:
            self.start_error = f"开桌失败: {type(e).__name__}: {e}"
            self.log(f"  [tongits] {self.start_error}")
            return False

    async def attach_ready_tongits_page(self, page: Any, *, target_url: str) -> bool:
        """接管用户已打开的 Tongits 主页面：只注入结算监听并启动自动出牌。"""
        coin_mod = _load_coin_smoke_module()
        if coin_mod is None:
            self.start_error = "未找到 test_k11_game_open_coin_smoke.py"
            self.log(f"  [tongits] {self.start_error}")
            return False
        case = next(
            (
                c
                for c in getattr(coin_mod, "GAME_CASES", ())
                if getattr(c, "game_id", "") == "tongits_king"
            ),
            None,
        )
        try:
            self.game_page = page
            self._owns_game_page = False
            self.log("  [tongits] 模式: 接管当前 Tongits 页面，不再自动进大厅/点入口/Join")
            self.log(f"  [tongits] 目标提示 URL: {target_url}")
            try:
                self.log(f"  [tongits] 当前页 URL: {page.url}")
            except Exception:
                self.log("  [tongits] 当前页 URL: <无法读取>")
            try:
                title = await page.title()
                self.log(f"  [tongits] 当前页标题: {title[:120]}")
            except Exception:
                pass
            try:
                viewport = page.viewport_size
                self.log(f"  [tongits] viewport: {viewport}")
            except Exception:
                pass
            try:
                frame_urls = [str(getattr(fr, "url", "") or "") for fr in page.frames]
                self.log(f"  [tongits] frame 数: {len(frame_urls)}")
                for i, fu in enumerate(frame_urls[:8]):
                    self.log(f"  [tongits] frame[{i}]: {fu[:180]}")
            except Exception as exc:
                self.log(f"  [tongits] frame 信息读取失败: {type(exc).__name__}: {exc}")
            try:
                detected_name, detected_uid = await _detect_tongits_page_identity(page)
                if detected_name or detected_uid:
                    old_name = self.my_name
                    if detected_name:
                        self.my_name = detected_name
                    if self._monitor is not None:
                        if detected_name:
                            self._monitor.my_name = detected_name.strip().lower()
                        if detected_uid:
                            self._monitor.my_uid = str(detected_uid)
                    self.log(
                        "  [tongits] 已从当前牌桌识别我方身份: "
                        f"name={detected_name or '-'} uid={detected_uid or '-'} "
                        f"（原配置 my_name={old_name!r}）"
                    )
                else:
                    self.log(
                        f"  [tongits] 未能从 URL/token 识别我方身份，继续使用 my_name={self.my_name!r}"
                    )
            except Exception as exc:
                self.log(f"  [tongits] 我方身份识别异常: {type(exc).__name__}: {exc}")
            try:
                entered, note = await _tongits_entry_signal(
                    page, coin_mod=coin_mod, case=case
                )
                self.log(
                    "  [tongits] 当前页面进场信号: "
                    f"{'已检测到' if entered else '未检测到'}"
                    + (f"（{note}）" if note else "")
                )
            except Exception as exc:
                self.log(f"  [tongits] 进场信号检测异常: {type(exc).__name__}: {exc}")
            try:
                await page.bring_to_front()
                self.log("  [tongits] 已将 Tongits 页签置前，准备交给物理点击执行器")
            except Exception as exc:
                self.log(f"  [tongits] 页签置前失败（继续启动 bot）: {type(exc).__name__}: {exc}")
            await self._inject_snippet_all_frames(page)
            self.log(
                f"  [tongits] 协议结算监听已注入所有 frame，回传端口: 127.0.0.1:{self.monitor_port}"
            )
            if self._inject_task is None:
                self._inject_task = asyncio.create_task(self._periodic_inject(page))
                self.log("  [tongits] 已启动周期性结算监听补注入任务")
            self._spawn_bot()
            self.log("  [tongits] 已接管当前牌桌，自动出牌运行中；等待协议结算")
            return True
        except Exception as e:
            self.start_error = f"接管当前牌桌失败: {type(e).__name__}: {e}"
            self.log(f"  [tongits] {self.start_error}")
            return False

    async def open_game_tab(self, context: Any, *, target_url: str) -> bool:
        coin_mod = _load_coin_smoke_module()
        if coin_mod is None:
            self.start_error = "未找到 test_k11_game_open_coin_smoke.py"
            self.log(f"  [tongits] {self.start_error}")
            return False
        try:
            self.game_page = await context.new_page()
            self._owns_game_page = True
            return await self.enter_tongits_on_page(self.game_page, target_url=target_url)
        except Exception as e:
            self.start_error = f"开桌失败: {type(e).__name__}: {e}"
            self.log(f"  [tongits] {self.start_error}（主冒烟继续）")
            if self.game_page is not None:
                try:
                    await self.game_page.close()
                except Exception:
                    pass
                self.game_page = None
            return False

    def wait_for_settlement(self, timeout_sec: float) -> dict[str, Any] | None:
        if self._last_settlement:
            return self._last_settlement
        if timeout_sec > 0:
            self._settle_event.wait(timeout=timeout_sec)
        if self._last_settlement:
            return self._last_settlement
        from_proto = self._load_recent_settlement_from_proto_status()
        if from_proto:
            self._last_settlement = from_proto
            return self._last_settlement
        from_log = self._load_recent_settlement_from_log()
        if from_log:
            self._last_settlement = from_log
            return self._last_settlement
        return self._last_settlement

    def _load_recent_settlement_from_log(self) -> dict[str, Any] | None:
        if self._require_bot_activity_before_settlement() and not self._bot_play_started:
            return None
        path = self.out_dir / "settlement.log"
        if not path.is_file():
            return None
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        except Exception:
            return None
        for line in reversed(lines):
            parsed = self._parse_settlement_log_line(line)
            if not parsed:
                continue
            ts = float(parsed.get("_ts") or 0.0)
            if ts and ts < self.started_at_wall - 8.0:
                continue
            if self._bot_play_started_wall and ts and ts < self._bot_play_started_wall - 1.0:
                continue
            parsed.pop("_ts", None)
            parsed["source"] = "settlement.log"
            self.log(f"  [tongits] 从 settlement.log 兜底读取结算: {parsed.get('line')}")
            return parsed
        return None

    def _load_recent_settlement_from_proto_status(self) -> dict[str, Any] | None:
        if self._require_bot_activity_before_settlement() and not self._bot_play_started:
            return None
        path = self.out_dir / "proto_status.json"
        if not path.is_file():
            return None
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
        line = str(obj.get("settlement_record_line") or "").strip()
        at_raw = str(obj.get("settlement_record_at") or "").strip()
        if line:
            parsed = self._parse_settlement_log_line(line)
            if parsed:
                ts = float(parsed.get("_ts") or 0.0)
                if ts and ts < self.started_at_wall - 8.0:
                    return None
                if self._bot_play_started_wall and ts and ts < self._bot_play_started_wall - 1.0:
                    return None
                parsed.pop("_ts", None)
                parsed["source"] = "proto_status"
                self.log(f"  [tongits] 从 proto_status 读取结算: {line}")
                return parsed
        settlement = str(obj.get("settlement") or "").strip()
        if settlement == "seen" and not self._proto_seen_without_record_logged:
            self._proto_seen_without_record_logged = True
            self.log(
                "  [tongits] proto_status 已看到结算信号，但还没有 settlement_record_line；"
                "说明协议帧未解析成本局金币结果"
                + (f"（settlement_at={at_raw}）" if at_raw else "")
            )
        return None

    @staticmethod
    def _parse_settlement_log_line(line: str) -> dict[str, Any] | None:
        m = re.search(
            r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[结算\]\s+"
            r"第(?P<game_no>\d+)局\s+(?P<cn>\S+)\s+我方\s+(?P<delta>[+-]?\d+|未知)\s+\|\s+"
            r"对手\s+(?P<opp>.*?)[\s|｜]+累计\s+净(?P<net>[+-]?\d+)\s+"
            r"胜(?P<wins>\d+)\s+负(?P<losses>\d+)\s+平(?P<draws>\d+)"
            r"[（(]msgType=(?P<msg_type>[^）)]+)[）)]",
            line,
        )
        if not m:
            return None
        try:
            dt = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone(timedelta(hours=8))
            )
            ts = dt.timestamp()
        except Exception:
            ts = 0.0
        cn = m.group("cn")
        outcome = {"胜": "WIN", "负": "LOSE", "平": "DRAW"}.get(cn, "UNKNOWN")
        raw_delta = m.group("delta")
        my_delta = None if raw_delta == "未知" else int(raw_delta)
        opponents: list[dict[str, Any]] = []
        for part in [p.strip() for p in m.group("opp").split("|") if p.strip()]:
            om = re.match(r"(?P<name>.*?)\s+(?P<delta>[+-]?\d+)$", part)
            if om:
                opponents.append(
                    {"name": om.group("name").strip(), "delta": int(om.group("delta"))}
                )
            else:
                opponents.append({"name": part, "delta": None})
        return {
            "_ts": ts,
            "game_no": int(m.group("game_no")),
            "outcome": outcome,
            "my_delta": my_delta,
            "opponents": opponents,
            "msg_type": m.group("msg_type"),
            "line": line,
            "net": int(m.group("net")),
            "wins": int(m.group("wins")),
            "losses": int(m.group("losses")),
            "draws": int(m.group("draws")),
        }

    def build_result_row(self, *, extra_wait_sec: float) -> dict[str, Any]:
        settlement = self.wait_for_settlement(extra_wait_sec)
        coin_ok = bool(
            settlement
            and settlement.get("my_delta") is not None
            and str(settlement.get("msg_type") or "3016") == "3016"
        )
        if settlement and settlement.get("my_delta") is None:
            coin_ok = False
        if self.start_error and not settlement:
            verdict = "SKIP"
            verdict_zh = "跳过"
        elif coin_ok:
            verdict = "PASS"
            verdict_zh = "通过"
        else:
            verdict = "FAIL"
            verdict_zh = "失败"

        detail = format_settlement_remark(settlement, coin_ok=coin_ok)
        if self.start_error and not settlement:
            detail = f"{detail} | 开桌: {self.start_error}"
        if self._bot_runtime_error:
            detail = f"{detail} | bot: {self._bot_runtime_error}"
        elapsed = time.monotonic() - self.started_at
        detail += f" | 并行时长 {elapsed:.0f}s"

        return {
            "case": "tongits_smoke_coin",
            "tier": "Tongits",
            "case_title_zh": "Tongits King 一局金币",
            "verdict": verdict,
            "verdict_zh": verdict_zh,
            "detail": detail[:8000],
            "executed_at": _smoke_executed_at_now(),
            "tongits_settlement": settlement,
            "coin_change_ok": coin_ok,
        }

    async def stop(self) -> None:
        self._stopped = True
        if self._inject_task is not None:
            self._inject_task.cancel()
            try:
                await self._inject_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self.game_page is not None and self._owns_game_page:
            try:
                await self.game_page.close()
            except Exception:
                pass
        elif self.game_page is not None:
            self.log("  [tongits] 保留用户已打开的 Tongits 页签，不自动关闭")
        if self.game_page is not None:
            self.game_page = None
        if self._bot_proc is not None:
            try:
                self._bot_proc.terminate()
            except Exception:
                pass
            self._bot_proc = None
        if self._http_server is not None:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
            self._http_server = None


async def tongits_smoke_start(
    context: Any,
    log: Callable[[str], None],
    *,
    target_url: str,
) -> TongitsSmokeSession | None:
    """启动并行 Tongits（失败返回 None 或部分启动的 session，不 raise）。"""
    my_name = (os.environ.get("K11_TONGITS_MY_NAME") or "victor").strip()
    port = int((os.environ.get("K11_TONGITS_MONITOR_PORT") or "17889").strip() or "17889")
    out_dir = SCRIPTS / "omnioutput"
    out_dir.mkdir(parents=True, exist_ok=True)

    log("———————— 并行：Tongits 一局 + 协议金币监控 ————————")
    session = TongitsSmokeSession(
        my_name=my_name,
        monitor_port=port,
        out_dir=out_dir,
        log=log,
    )
    if not session.start_monitor_server():
        return session
    await session.open_game_tab(context, target_url=target_url)
    return session


async def tongits_smoke_finalize_and_append(
    session: TongitsSmokeSession | None,
    results: list[dict[str, Any]],
    log: Callable[[str], None],
) -> None:
    if session is None:
        return
    log("")
    log("———————— Tongits 并行局 · 收尾采集 ————————")
    extra_wait = _env_float("K11_TONGITS_ROUND_WAIT_SEC", 90.0)
    try:
        row = session.build_result_row(extra_wait_sec=extra_wait)
        results.append(row)
        log(
            f"  [tongits] 并入 {row['case']!r} → {row['verdict_zh']}（{row['verdict']}）"
        )
        log(f"  [tongits] 备注: {row['detail'][:320]}")
    except Exception as e:
        log(f"  [tongits] 收尾异常（已记 SKIP，不阻断）: {type(e).__name__}: {e}")
        results.append(
            {
                "case": "tongits_smoke_coin",
                "tier": "Tongits",
                "case_title_zh": "Tongits King 一局金币",
                "verdict": "SKIP",
                "verdict_zh": "跳过",
                "detail": f"游戏金币变化: 失败 | 收尾异常: {e}",
                "executed_at": _smoke_executed_at_now(),
            }
        )
    finally:
        try:
            await session.stop()
        except Exception:
            pass


def filter_results_for_exit_code(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if tongits_affects_exit_code():
        return results
    return [r for r in results if r.get("case") != "tongits_smoke_coin"]


K11_DEFAULT_LARK_WIKI_URL = (
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZyWlwhdW1iNQuykvy7qlw93sgTe"
)


def send_tongits_lark_notification(
    row: dict[str, Any],
    *,
    target_url: str,
    log: Callable[[str], None],
    no_lark: bool = False,
    lark_wiki_url: str = "",
) -> None:
    """独立 Tongits 冒烟：只发一条结算行到 Lark 卡片。"""
    if no_lark:
        log("  [lark] 已跳过：--no-lark-report")
        return
    lark_path = SCRIPTS / "k11_lark_smoke_report.py"
    mod = _load_script_module(lark_path, "k11_lark_smoke_report_tongits_embed")
    if mod is None:
        log(f"  [lark] 未找到或无法加载 {lark_path}，跳过通知")
        return
    try:
        from l3_node.packaged_lark_env import apply_packaged_lark_to_os_environ

        apply_packaged_lark_to_os_environ()
    except Exception:
        pass
    wiki = (
        (lark_wiki_url or "").strip()
        or (os.environ.get("K11_SMOKE_LARK_WIKI_URL") or "").strip()
        or K11_DEFAULT_LARK_WIKI_URL
    )
    v = str(row.get("verdict") or "SKIP").upper()
    vzh = str(row.get("verdict_zh") or ("通过" if v == "PASS" else "失败"))
    lark_row = {
        "tier": str(row.get("tier") or "Tongits"),
        "case": str(row.get("case") or "tongits_autoplay_smoke"),
        "case_title_zh": str(row.get("case_title_zh") or "Tongits 自动打牌金币"),
        "verdict": v,
        "verdict_zh": vzh,
        "detail": str(row.get("detail") or "")[:8000],
    }
    app_id = (os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip()
    app_secret = (os.environ.get("K11_SMOKE_LARK_APP_SECRET") or "").strip()
    chat_id = (os.environ.get("K11_SMOKE_LARK_NOTIFY_CHAT_ID") or "").strip()
    try:
        mod.send_k11_smoke_lark_notification(  # type: ignore[attr-defined]
            results=[lark_row],
            target_url=target_url,
            wiki_url=wiki,
            lark_wrote=0,
            app_id=app_id,
            app_secret=app_secret,
            chat_id=chat_id,
            log=log,
        )
        log("  [lark] Tongits 金币结算卡片已发送")
    except Exception as e:
        log(f"  [lark] 发送异常（不阻断本地结果）: {e}")

