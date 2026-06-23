#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 胜负结算「全自动」抓取（Chrome DevTools Protocol，无需 F12 / 无需粘贴）。

为什么用 CDP：
  游戏的数据 WebSocket 在页面加载时已建立、且通过“早已缓存的 console 引用”打印
  （形如 console.log("【ws】收到消息：", 3024, {msgType,body})）。事后注入 JS 覆盖
  console 拦不到这些日志。CDP 的 Runtime.consoleAPICalled 在 V8 层捕获“所有”
  console.* 调用，无视是否被缓存，因此能稳定拿到游戏协议帧并解析金币/胜负。

复用 tongits_result_monitor.ResultMonitor 做解析 / 记账 / 打印（噪声过滤、按 uid 定位本人、
结算去重、CSV/JSONL 落盘、自学习日志全部一致）。

两种用法：
  A) 全自动启动 Chrome（推荐，首次登录后会记住）：
     python scripts/tongits_cdp_capture.py --launch --url "https://www.herontest.xin/..."
  B) 附着到已用调试端口启动的 Chrome：
     先 chrome.exe --remote-debugging-port=9222 --user-data-dir=...
     再 python scripts/tongits_cdp_capture.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests
import websocket  # websocket-client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tongits_result_monitor import ResultMonitor  # noqa: E402

# 页面内安全序列化（去函数/循环/限深），用于把 console 的对象参数取回 Python。
_SERIALIZE_FN = """
function(){
  const seen = new WeakSet();
  const f = (v, d) => {
    if (d > 7 || v == null) return v == null ? null : undefined;
    const t = typeof v;
    if (t === 'number' || t === 'string' || t === 'boolean') return v;
    if (t !== 'object') return undefined;
    if (seen.has(v)) return undefined; seen.add(v);
    if (Array.isArray(v)) return v.slice(0, 60).map((x) => f(x, d + 1));
    const o = {}; let n = 0;
    for (const k in v) { if (++n > 80) break; try { const pv = f(v[k], d + 1); if (pv !== undefined) o[k] = pv; } catch (e) {} }
    return o;
  };
  try { return f(this, 0); } catch (e) { return { __serr: String(e) }; }
}
"""

_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_chrome(explicit: str | None) -> str | None:
    if explicit and Path(explicit).exists():
        return explicit
    for p in _CHROME_CANDIDATES:
        if p and Path(p).exists():
            return p
    return None


def _launch_chrome(chrome: str, port: int, profile_dir: Path, url: str | None) -> subprocess.Popen | None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",  # 新版 Chrome：允许 CDP WebSocket 跨源连接
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        args.append(url)
    print(f"[cdp] 启动 Chrome：{chrome}\n[cdp] 调试端口={port} 配置目录={profile_dir}", flush=True)
    return subprocess.Popen(args)


def _list_targets(host: str, port: int) -> list[dict[str, Any]]:
    r = requests.get(f"http://{host}:{port}/json", timeout=3)
    r.raise_for_status()
    return r.json()


def _pick_target(targets: list[dict[str, Any]], url_filter: str) -> dict[str, Any] | None:
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if url_filter:
        flt = url_filter.lower()
        for t in pages:
            if flt in str(t.get("url", "")).lower() or flt in str(t.get("title", "")).lower():
                return t
    # 退而求其次：第一个非 devtools/about 的页面
    for t in pages:
        u = str(t.get("url", ""))
        if u and not u.startswith(("devtools://", "about:", "chrome://")):
            return t
    return pages[0] if pages else None


def _bridge_snippet_js(port: int = 17888) -> str:
    """读取 browser_proto_bridge_snippet.js，替换 bridge 端口。"""
    path = Path(__file__).resolve().parent / "browser_proto_bridge_snippet.js"
    raw = path.read_text(encoding="utf-8")
    url = f"http://127.0.0.1:{int(port)}/proto/update"
    return raw.replace("http://127.0.0.1:17888/proto/update", url)


def inject_proto_bridge_hooks(
    cdp: "CDPClient",
    session_id: str | None,
    *,
    url: str = "",
    target_type: str = "",
    port: int = 17888,
) -> bool:
    """经 CDP 向页面/iframe 自动注入 proto-bridge（无需 F12 粘贴）。"""
    if (target_type or "").lower() == "service_worker":
        return False
    inj_key = session_id or "__root__"
    if inj_key in cdp._bridge_injected_sessions:
        return False
    url_l = (url or "").lower()
    if url_l and not any(k in url_l for k in ("tongits", "ejoyplay", "herontest", "game-frame", "game_id")):
        return False
    js = _bridge_snippet_js(port)
    try:
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": js}, session_id=session_id)
        cdp.call("Runtime.evaluate", {"expression": js, "returnByValue": False}, session_id=session_id)
        cdp._bridge_injected_sessions.add(inj_key)
        print(f"[cdp] proto-bridge 已自动注入（port={port}） url={url[:56]}", flush=True)
        return True
    except Exception as exc:
        print(f"[cdp] proto-bridge 自动注入失败: {exc}", flush=True)
        return False


class CDPClient:
    """会话感知、重入安全的 CDP 客户端：自动附着所有子目标(iframe/OOPIF/worker)，
    在每个会话上订阅 console，确保内层游戏引擎(独立进程 iframe)的协议帧也能抓到。"""

    def __init__(
        self,
        ws_url: str,
        on_console,
        *,
        bridge_auto_inject: bool = False,
        bridge_port: int = 17888,
    ) -> None:
        self.ws = websocket.create_connection(
            ws_url, max_size=None, enable_multithread=True, suppress_origin=True
        )
        self._id = 0
        self._results: dict[int, Any] = {}
        self._on_console = on_console
        self.console_events = 0
        self._sessions: set[str] = set()
        self._handling_console = 0
        self._deferred_console: list[tuple[dict[str, Any], str | None]] = []
        self._bridge_auto_inject = bridge_auto_inject
        self._bridge_port = int(bridge_port)
        self._bridge_injected_sessions: set[str] = set()

    def call(self, method: str, params: dict[str, Any] | None = None,
             session_id: str | None = None) -> Any:
        self._id += 1
        mid = self._id
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg))
        while mid not in self._results:
            self._pump_one()
        return self._results.pop(mid)

    def _enable_session(self, session_id: str | None) -> None:
        try:
            self.call("Runtime.enable", {}, session_id=session_id)
        except Exception:
            pass
        # 继续向下传播自动附着，递归覆盖更深层 iframe
        try:
            self.call("Target.setAutoAttach",
                      {"autoAttach": True, "flatten": True, "waitForDebuggerOnStart": False},
                      session_id=session_id)
        except Exception:
            pass

    def _dispatch_console(self, params: dict[str, Any], session_id: str | None) -> None:
        """处理 console 事件；嵌套 CDP call 期间新事件入队，避免 handler→serialize→call 栈溢出。"""
        self._handling_console += 1
        try:
            self._on_console(self, params, session_id)
        except Exception as exc:
            print(f"[cdp] console handler error: {exc}", flush=True)
        finally:
            self._handling_console -= 1
            if self._handling_console == 0 and self._deferred_console:
                pending = self._deferred_console[:]
                self._deferred_console.clear()
                for p, sid in pending:
                    self._dispatch_console(p, sid)

    def _pump_one(self) -> None:
        raw = self.ws.recv()
        if not raw:
            return
        try:
            msg = json.loads(raw)
        except Exception:
            return
        if "id" in msg:
            self._results[msg["id"]] = msg.get("result", msg.get("error"))
            return
        method = msg.get("method")
        sid = msg.get("sessionId")
        params = msg.get("params") or {}
        if method == "Runtime.consoleAPICalled":
            self.console_events += 1
            if self._handling_console > 0:
                self._deferred_console.append((params, sid))
                return
            self._dispatch_console(params, sid)
        elif method == "Target.attachedToTarget":
            child_sid = params.get("sessionId")
            info = params.get("targetInfo") or {}
            if child_sid and child_sid not in self._sessions:
                self._sessions.add(child_sid)
                print(f"[cdp] 附着子目标 type={info.get('type')} url={str(info.get('url',''))[:60]}",
                      flush=True)
                self._enable_session(child_sid)
                if self._bridge_auto_inject:
                    inject_proto_bridge_hooks(
                        self, child_sid,
                        url=str(info.get("url") or ""),
                        target_type=str(info.get("type") or ""),
                        port=self._bridge_port,
                    )

    def serialize_object(self, object_id: str, session_id: str | None = None) -> Any:
        res = self.call("Runtime.callFunctionOn", {
            "objectId": object_id,
            "functionDeclaration": _SERIALIZE_FN,
            "returnByValue": True,
            "awaitPromise": False,
        }, session_id=session_id)
        if isinstance(res, dict):
            return (res.get("result") or {}).get("value")
        return None

    def run_forever(self) -> None:
        while True:
            self._pump_one()


_GAME_MSG_TYPES = frozenset(
    {str(x) for x in range(3010, 3035)} | {"3016", "3017", "3018", "3021", "3024"}
)


def _build_console_handler(monitor: ResultMonitor):
    """把一次 console.* 调用转成 ResultMonitor 期望的 payload，并喂给它。"""
    GAME_LABEL = ("【ws】", "WebSDK", "Messager", "GameRoom", "C2N_", "N2C_", "W2C_", "C2W_")

    def handler(cdp: CDPClient, params: dict[str, Any], session_id: str | None = None) -> None:
        args = params.get("args") or []
        texts: list[str] = []
        nums: list[int] = []
        objs: list[dict[str, Any]] = []
        for a in args:
            t = a.get("type")
            if t in ("string", "number", "boolean"):
                texts.append(str(a.get("value")))
                if t == "number":
                    try:
                        nums.append(int(a.get("value")))
                    except (TypeError, ValueError):
                        pass
            elif t == "object" and a.get("objectId"):
                objs.append(a)
        text = " ".join(texts)

        # 便宜的预筛：要么字符串含游戏标签，要么对象 preview 里出现 msgType/type
        label_hit = any(s in text for s in GAME_LABEL)
        preview_game = False
        for a in objs:
            prev = a.get("preview") or {}
            for p in prev.get("properties", []) or []:
                if p.get("name") in ("msgType", "type"):
                    preview_game = True
                    break
            if preview_game:
                break
        if not (label_hit or preview_game):
            return

        parsed: Any = None
        for a in objs:
            val = cdp.serialize_object(a["objectId"], session_id=session_id)
            if isinstance(val, (dict, list)):
                parsed = val
                break

        payload: dict[str, Any] = {
            "kind": "ws",
            "direction": "out" if any(k in text for k in ("发送", "Send", "C2N_", "C2W_")) else "in",
            "text": text[:4000],
        }
        if isinstance(parsed, dict):
            if parsed.get("msgType") is not None:
                payload["msgType"] = parsed["msgType"]
            elif parsed.get("type") is not None:
                payload["type"] = parsed["type"]
            if parsed.get("body") is not None:
                payload["body"] = parsed["body"]
            elif parsed.get("data") is not None:
                payload["body"] = parsed["data"]
            else:
                payload["body"] = parsed
        elif parsed is not None:
            payload["body"] = parsed

        # 游戏 console 常见形态：log("【ws】收到消息：", 3016, {body...}) — msgType 在数字参数里
        if payload.get("msgType") in (None, "") and nums:
            for n in nums:
                if str(n) in _GAME_MSG_TYPES or 3010 <= n <= 3035:
                    payload["msgType"] = str(n)
                    break

        monitor.handle(payload)

    return handler


def main() -> int:
    ap = argparse.ArgumentParser(description="Tongits 结算全自动抓取（CDP）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9222, help="Chrome 远程调试端口")
    ap.add_argument("--url-filter", default="herontest", help="用 url/标题关键字挑选游戏标签页")
    ap.add_argument("--my-name", default="victor")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "omnioutput"))
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--launch", action="store_true", help="自动启动带调试端口的 Chrome")
    ap.add_argument("--chrome", default=None, help="chrome.exe 路径（默认自动探测）")
    ap.add_argument("--url", default=None, help="--launch 时打开的游戏 URL")
    ap.add_argument(
        "--profile-dir",
        default=str(Path(__file__).resolve().parent / "omnioutput" / "cdp_chrome_profile"),
    )
    args = ap.parse_args()

    launched = False
    if args.launch:
        # 幂等：若调试端口已就绪（上次启动的 Chrome 还开着），直接复用现有页面，不再新开。
        already = False
        try:
            if _list_targets(args.host, args.port):
                already = True
        except Exception:
            already = False
        if already:
            print(f"[cdp] 检测到调试 Chrome 已在运行（端口 {args.port}），复用现有页面，不新开。",
                  flush=True)
        else:
            chrome = _find_chrome(args.chrome)
            if not chrome:
                print("[cdp] 未找到 Chrome/Edge，请用 --chrome 指定 chrome.exe 路径。", flush=True)
                return 2
            _launch_chrome(chrome, args.port, Path(args.profile_dir), args.url)
            launched = True

    # 等待调试端口就绪
    target = None
    deadline = time.time() + (40 if launched else 8)
    while time.time() < deadline:
        try:
            targets = _list_targets(args.host, args.port)
            target = _pick_target(targets, args.url_filter)
            if target:
                break
        except Exception:
            pass
        time.sleep(1.0)

    if not target:
        print(
            f"[cdp] 未能在 http://{args.host}:{args.port} 找到目标页面。\n"
            f"      请确认 Chrome 以 --remote-debugging-port={args.port} 启动，"
            f"且已打开游戏页（--url-filter 当前='{args.url_filter}'）。",
            flush=True,
        )
        return 3

    print(f"[cdp] 命中目标：{target.get('title','')[:40]} | {str(target.get('url',''))[:80]}", flush=True)

    monitor = ResultMonitor(Path(args.out_dir), args.my_name, args.discover)
    print(
        f"[cdp] 全自动抓取已启动（无需 F12）。我方={args.my_name} discover={args.discover}\n"
        f"[cdp] CSV={monitor.csv_path}",
        flush=True,
    )

    handler = _build_console_handler(monitor)
    cdp = CDPClient(target["webSocketDebuggerUrl"], handler)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    # 自动附着所有子目标(iframe/OOPIF/worker)，并在每个会话上订阅 console。
    # 内层游戏引擎(带金币的 30xx 帧)通常在独立进程 iframe 里，必须靠它才能抓到。
    cdp.call("Target.setAutoAttach",
             {"autoAttach": True, "flatten": True, "waitForDebuggerOnStart": False})
    print("[cdp] 已订阅主页面 + 自动附着子 iframe(含内层游戏引擎)。开始捕获…", flush=True)
    try:
        cdp.run_forever()
    except KeyboardInterrupt:
        print("\n[cdp] 退出。", flush=True)
        if monitor.type_counts:
            for mt, c in sorted(monitor.type_counts.items(), key=lambda kv: -kv[1]):
                print(f"    msgType={mt:<16} {c} 帧", flush=True)
        h = monitor.health()
        print(f"[cdp] 共 {h['games']} 局：胜{h['wins']} 负{h['losses']} 平{h['draws']} 净{h['net']:+d}", flush=True)
    except Exception as exc:
        print(f"[cdp] 连接中断：{exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
