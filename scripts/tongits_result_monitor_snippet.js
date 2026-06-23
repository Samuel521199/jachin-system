/*
Tongits 胜负结算监控 —— 浏览器侧采集片段（纯协议，不依赖视觉）。

【重要】进牌局后“尽早”粘贴执行一次（在本局结束之前），否则结算帧已经刷过去就抓不到。
页面刷新后需要重新粘贴。

它把协议帧转发到本地监控： http://127.0.0.1:17889/proto/update （由 tongits_result_monitor.py 接收）

已确认该游戏 WebSDK 的协议形态（来自实机 F12）：
  收： console.log("【ws】收到消息：", 3035, {msgType:3035, requestId, errorCode, body:{…}})
  发： console.log("【WebSDK Send】", {type:'C2W_GAME_STATUS', data:{params:{userId, seat}, status}})
因此主力来源是 console 钩子；WebSocket 多为二进制(Protobuf)，按 hex 预览兜底转发。
*/
(() => {
  const BRIDGE_URL = "http://127.0.0.1:17889/proto/update";

  // 幂等可重装：把“最原始”的 console 方法存到 window，重复粘贴时先还原再重新 hook，
  // 避免旧版本残留 / 多层包裹 / 早期 inFlight 死锁导致再也收不到。
  if (!window.__rmRawConsole) {
    window.__rmRawConsole = {
      log: console.log.bind(console),
      info: (console.info || console.log).bind(console),
      debug: (console.debug || console.log).bind(console),
      warn: (console.warn || console.log).bind(console),
    };
  } else if (window.__rmRestore) {
    // 还原上一次的包裹，避免层层叠加
    try { window.__rmRestore(); } catch {}
  }
  const RAW = window.__rmRawConsole;
  window.__tongitsResultMonitorInstalled = true;

  const KEEP_RE = /(【ws】|WebSDK|msgType|C2W_|W2C_|joinRoom|settle|settlement|result|reward|round|game_?end|gameover|finish|win|lose|coin|gold|chip|balance|score)/i;

  const safeJsonParse = (v) => {
    if (typeof v !== "string") return null;
    try { return JSON.parse(v); } catch { return null; }
  };

  const send = (payload) => {
    const body = JSON.stringify(payload);
    // 以 fetch 为主（实测可达本地 17889）；失败再用 sendBeacon 兜底。
    try {
      fetch(BRIDGE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        keepalive: true,
        mode: "cors",
      }).catch(() => {
        try { navigator.sendBeacon && navigator.sendBeacon(BRIDGE_URL, new Blob([body], { type: "application/json" })); } catch {}
      });
    } catch {
      try { navigator.sendBeacon && navigator.sendBeacon(BRIDGE_URL, new Blob([body], { type: "application/json" })); } catch {}
    }
  };

  // 安全深拷贝（去函数/原型链/循环），最多 6 层
  const plain = (v, depth = 0) => {
    if (depth > 6 || v == null) return v == null ? null : undefined;
    const t = typeof v;
    if (t === "number" || t === "string" || t === "boolean") return v;
    if (t !== "object") return undefined;
    if (Array.isArray(v)) return v.slice(0, 30).map((x) => plain(x, depth + 1));
    const o = {};
    let n = 0;
    for (const k in v) {
      if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
      if (++n > 40) break;
      const pv = plain(v[k], depth + 1);
      if (pv !== undefined) o[k] = pv;
    }
    return o;
  };

  const enrich = (payload, parsed) => {
    const p = parsed && typeof parsed === "object" ? plain(parsed) : null;
    if (p && typeof p === "object") {
      if (p.msgType != null) payload.msgType = p.msgType;
      else if (p.type != null) payload.type = p.type;
      if (p.requestId != null) payload.requestId = p.requestId;
      if (p.errorCode != null) payload.errorCode = p.errorCode;
      if (p.body != null) payload.body = p.body;
      else if (p.data != null) payload.body = p.data;
      else payload.body = p; // 整个对象当 body，便于服务端深度解析
    }
    return payload;
  };

  const toHexPreview = (buf) => {
    try {
      const u8 = new Uint8Array(buf);
      const n = Math.min(u8.length, 64);
      let s = "";
      for (let i = 0; i < n; i++) s += u8[i].toString(16).padStart(2, "0");
      return `[hex:${u8.length}]` + s;
    } catch { return "[binary]"; }
  };

  // Agora/WebRTC 语音 SDK 遥测特征：命中即丢弃（与牌局胜负无关，否则刷屏淹没真协议）。
  const isRtcNoise = (obj) => obj && typeof obj === "object" && (
    obj._id !== undefined || obj._message !== undefined || obj._result !== undefined ||
    obj._type !== undefined || obj.ortc !== undefined || obj.ap_response !== undefined ||
    obj.rejoin_token !== undefined || obj.peer_delay !== undefined || obj.B_acd !== undefined ||
    obj.iceParameters !== undefined || obj.rtpCapabilities !== undefined || obj.sdk_version !== undefined
  );
  // 游戏帧判定：数字 msgType / 形如 C2N_、N2C_ 的 type / 或带【ws】WebSDK Messager 标签。
  const TYPE_RE = /^[A-Za-z]2[A-Za-z]_/;
  const isGameObj = (obj) => obj && typeof obj === "object" && (
    obj.msgType != null || (typeof obj.type === "string" && TYPE_RE.test(obj.type))
  );

  // -- console 钩子（主力来源：SDK 直接 console 打印解码后的对象）--
  const makeHook = (raw, level) => function (...args) {
    try {
      const txt = args.filter((x) => typeof x === "string").join(" ");
      if (txt.indexOf("[rm]") !== -1) return raw.apply(console, args); // 跳过本工具自身日志
      const obj = args.find((x) => x && typeof x === "object");
      const labelHit = /(【ws】|WebSDK|Messager|GameRoom)/i.test(txt);
      const game = isGameObj(obj) || (labelHit && obj);
      if (game && !isRtcNoise(obj)) {
        const payload = enrich(
          { kind: "ws", level, direction: /发送|Send|C2N_|C2W_/i.test(txt) ? "out" : "in", text: txt.slice(0, 4000) },
          obj,
        );
        send(payload);
      }
    } catch {}
    return raw.apply(console, args);
  };
  console.log = makeHook(RAW.log, "log");
  console.info = makeHook(RAW.info, "info");
  console.debug = makeHook(RAW.debug, "debug");
  console.warn = makeHook(RAW.warn, "warn");
  window.__rmRestore = () => {
    console.log = RAW.log;
    console.info = RAW.info;
    console.debug = RAW.debug;
    console.warn = RAW.warn;
  };

  // -- WebSocket 钩子（兜底：捕获新建 socket；二进制按 hex 预览）--
  const NativeWS = window.WebSocket;
  function PatchedWS(url, protocols) {
    const ws = protocols ? new NativeWS(url, protocols) : new NativeWS(url);
    const wrap = async (data, direction) => {
      try {
        let text;
        if (typeof data === "string") {
          text = data;
        } else if (data instanceof Blob) {
          const ab = await data.arrayBuffer();
          try { text = new TextDecoder("utf-8", { fatal: true }).decode(ab); }
          catch { text = toHexPreview(ab); }
        } else if (data instanceof ArrayBuffer) {
          try { text = new TextDecoder("utf-8", { fatal: true }).decode(data); }
          catch { text = toHexPreview(data); }
        } else {
          text = String(data);
        }
        const parsed = safeJsonParse(text);
        // 仅转发能解析出“游戏 msgType / C2N_ 类型”的帧；丢弃 RTC 噪声与纯二进制媒体流。
        if (!parsed || isRtcNoise(parsed) || !isGameObj(parsed)) return;
        send(enrich({ kind: "ws", source: "wshook", direction, url: String(url || ""), text: text.slice(0, 8000) }, parsed));
      } catch {}
    };
    try {
      ws.addEventListener("message", (ev) => wrap(ev.data, "in"));
      const nativeSend = ws.send;
      ws.send = function (data) { wrap(data, "out"); return nativeSend.apply(this, arguments); };
    } catch {}
    return ws;
  }
  PatchedWS.prototype = NativeWS.prototype;
  PatchedWS.CONNECTING = NativeWS.CONNECTING;
  PatchedWS.OPEN = NativeWS.OPEN;
  PatchedWS.CLOSING = NativeWS.CLOSING;
  PatchedWS.CLOSED = NativeWS.CLOSED;
  window.WebSocket = PatchedWS;

  // 安装即自检：立刻给监控发一条，确认链路联通（监控会打印一行 [新类型] msgType=__rm_selftest__）
  send({ kind: "meta", msgType: "__rm_selftest__", direction: "in",
         text: "rm selftest", body: { rmSelfTest: 1, href: String(location.href).slice(0, 200) } });

  RAW.log("[rm] hook ready（已重装+自检上报）-> bridge 17889。监控若收到 __rm_selftest__ 即链路通。");
})();
