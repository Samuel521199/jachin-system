/*
Auto-injected by bot CDP (tongits_proto_settlement_service) on game iframe attach.
Manual paste in DevTools is only needed when NOT using debug Chrome / CDP.
Forwards API/WS events to local bridge: http://127.0.0.1:17888/proto/update
*/
(() => {
  if (window.__tongitsProtoBridgeInstalled) {
    console.log("[proto-bridge] already installed");
    return;
  }
  window.__tongitsProtoBridgeInstalled = true;

  const BRIDGE_URL = "http://127.0.0.1:17888/proto/update";
  const GAME_RE = /(msgType|C2W_|W2C_|duel|fight|settle|result|reward|coin|gold|balance)/i;
  const safeJsonParse = (v) => {
    if (typeof v !== "string") return null;
    try { return JSON.parse(v); } catch { return null; }
  };
  const send = (payload) => {
    try {
      fetch(BRIDGE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch(() => {});
    } catch {}
  };

  const _fetch = window.fetch;
  window.fetch = async function(input, init = {}) {
    const url = typeof input === "string" ? input : input?.url || "";
    const method = (init.method || "GET").toUpperCase();
    const resp = await _fetch.apply(this, arguments);
    if (GAME_RE.test(url)) {
      send({
        kind: "api",
        method,
        url,
        status: resp.status,
      });
    }
    return resp;
  };

  const _open = XMLHttpRequest.prototype.open;
  const _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__protoMeta = { method, url };
    return _open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener("loadend", () => {
      const m = this.__protoMeta || {};
      const url = String(m.url || "");
      if (!GAME_RE.test(url)) return;
      send({
        kind: "api",
        method: m.method || "GET",
        url,
        status: this.status,
      });
    });
    return _send.apply(this, arguments);
  };

  const _log = console.log;
  console.log = function(...args) {
    try {
      const text = args.filter((x) => typeof x === "string").join(" ");
      if (/【ws】收到消息|【ws】发送消息|【WebSDK Send】|joinRoomRes/.test(text)) {
        const obj = args.find((x) => x && typeof x === "object");
        const payload = {
          kind: "ws",
          direction: /发送|WebSDK Send/.test(text) ? "out" : "in",
          text,
        };
        if (obj && typeof obj === "object") {
          if (obj.msgType != null) payload.msgType = obj.msgType;
          if (obj.errorCode != null) payload.errorCode = obj.errorCode;
          if (obj.body != null) payload.body = obj.body;
          if (obj.type != null && payload.msgType == null) payload.type = obj.type;
          if (obj.data != null && payload.body == null) payload.body = obj.data;
        }
        send(payload);
      }
    } catch {}
    return _log.apply(this, args);
  };

  // Hook WebSocket directly (比依赖 console 文本更稳定)
  const NativeWS = window.WebSocket;
  window.WebSocket = function(url, protocols) {
    const ws = protocols ? new NativeWS(url, protocols) : new NativeWS(url);
    try {
      ws.addEventListener("message", async (ev) => {
        let data = ev.data;
        if (data instanceof Blob) {
          try { data = await data.text(); } catch {}
        }
        if (data instanceof ArrayBuffer) {
          try { data = new TextDecoder("utf-8").decode(data); } catch {}
        }
        const parsed = safeJsonParse(data);
        const payload = {
          kind: "ws",
          direction: "in",
          url: String(url || ""),
          text: typeof data === "string" ? data.slice(0, 1000) : "[binary]",
        };
        if (parsed && typeof parsed === "object") {
          if (parsed.msgType != null) payload.msgType = parsed.msgType;
          if (parsed.requestId != null) payload.requestId = parsed.requestId;
          if (parsed.errorCode != null) payload.errorCode = parsed.errorCode;
          if (parsed.body != null) payload.body = parsed.body;
          if (payload.msgType == null && parsed.type != null) payload.type = parsed.type;
          if (payload.body == null && parsed.data != null) payload.body = parsed.data;
        }
        if (GAME_RE.test(payload.text) || payload.msgType != null) {
          send(payload);
        }
      });
      const nativeSend = ws.send;
      ws.send = function(data) {
        try {
          const parsed = safeJsonParse(typeof data === "string" ? data : "");
          const payload = {
            kind: "ws",
            direction: "out",
            url: String(url || ""),
            text: typeof data === "string" ? data.slice(0, 1000) : "[binary]",
          };
          if (parsed && typeof parsed === "object") {
            if (parsed.msgType != null) payload.msgType = parsed.msgType;
            if (parsed.type != null && payload.msgType == null) payload.type = parsed.type;
            if (parsed.body != null) payload.body = parsed.body;
            if (parsed.data != null && payload.body == null) payload.body = parsed.data;
          }
          if (GAME_RE.test(payload.text) || payload.msgType != null) {
            send(payload);
          }
        } catch {}
        return nativeSend.apply(this, arguments);
      };
    } catch {}
    return ws;
  };
  window.WebSocket.prototype = NativeWS.prototype;
  window.WebSocket.CONNECTING = NativeWS.CONNECTING;
  window.WebSocket.OPEN = NativeWS.OPEN;
  window.WebSocket.CLOSING = NativeWS.CLOSING;
  window.WebSocket.CLOSED = NativeWS.CLOSED;

  console.log("[proto-bridge] browser hook installed:", BRIDGE_URL);
})();
