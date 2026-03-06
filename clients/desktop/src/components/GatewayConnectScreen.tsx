/**
 * V2 L3-L2 零信任配对 - 网关接驳界面
 * 用户输入 L2 网关地址，发起神经接驳，等待管理员审批
 */

import { useState, useCallback, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Loader2, Zap, AlertCircle } from "lucide-react";

const DEFAULT_L2 = "http://localhost:18888";
const POLL_INTERVAL_MS = 2000;

interface GatewayConnectScreenProps {
  onPaired: () => void;
}

export function GatewayConnectScreen({ onPaired }: GatewayConnectScreenProps) {
  const [l2Url, setL2Url] = useState(DEFAULT_L2);
  const [deviceName, setDeviceName] = useState("");
  const [step, setStep] = useState<"idle" | "connecting" | "waiting" | "success" | "error">("idle");
  const [error, setError] = useState("");

  const loadSavedUrl = useCallback(async () => {
    try {
      const url = await invoke<string>("read_l2_gateway_url");
      if (url) setL2Url(url);
    } catch {
      // 使用默认值
    }
  }, []);

  useEffect(() => {
    loadSavedUrl();
  }, [loadSavedUrl]);

  // 已审批节点：L3 可能已被 setup 自动启动，轮询 is_gateway_paired 自动进入主界面
  useEffect(() => {
    if (step !== "idle") return;
    const timer = setInterval(async () => {
      try {
        const p = await invoke<boolean>("is_gateway_paired");
        if (p) onPaired();
      } catch {
        // ignore
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [step, onPaired]);

  const startConnect = useCallback(async () => {
    const url = l2Url.trim().replace(/\/+$/, "") || DEFAULT_L2;
    const name = deviceName.trim() || undefined;
    setStep("connecting");
    setError("");

    try {
      await invoke("write_l2_gateway_config", { input: { url, displayName: name || undefined } });
      await invoke("gateway_connect", { input: { l2Url: url, displayName: name ?? null } });
      setStep("waiting");
      // 轮询检测 L2 是否已审批（paired=true），而非仅检测 WebSocket 端口
      const checkReady = async () => {
        try {
          const paired = await invoke<boolean>("is_gateway_paired");
          if (paired) {
            setStep("success");
            setTimeout(() => onPaired(), 1200);
            return true;
          }
        } catch {
          // 继续轮询
        }
        return false;
      };

      const poll = async () => {
        for (let i = 0; i < 300; i++) {
          if (await checkReady()) return;
          await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        }
        setStep("error");
        setError("配对超时：L2 管理员未在限定时间内审批");
      };
      poll();
    } catch (e) {
      setError(String(e));
      setStep("error");
    }
  }, [l2Url, deviceName, onPaired]);

  return (
    <div className="h-screen w-screen flex flex-col items-center justify-center bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white overflow-hidden">
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(34, 211, 238, 0.08) 0%, transparent 60%),
            radial-gradient(ellipse 60% 40% at 50% 80%, rgba(139, 92, 246, 0.06) 0%, transparent 50%)
          `,
        }}
      />

      <div className="relative z-10 flex flex-col items-center max-w-md px-8">
        <h1 className="text-2xl font-bold text-cyan-400/95 mb-1 tracking-wide">
          L2 网关神经接驳
        </h1>
        <p className="text-sm text-white/50 mb-8">
          Zero-Trust · 向 L2 宣誓效忠
        </p>

        {step === "idle" && (
          <div className="w-full space-y-4">
            <div>
              <label className="block text-xs text-cyan-400/80 uppercase tracking-wider mb-2">
                Layer 2 网关地址
              </label>
              <input
                type="url"
                value={l2Url}
                onChange={(e) => setL2Url(e.target.value)}
                placeholder="http://192.168.1.100:18888"
                className="w-full px-4 py-3 rounded-xl bg-black/40 border border-cyan-500/30 text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-cyan-500/50 focus:border-cyan-500 font-mono text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-cyan-400/80 uppercase tracking-wider mb-2">
                设备名称（可选，便于 L2 审批识别）
              </label>
              <input
                type="text"
                value={deviceName}
                onChange={(e) => setDeviceName(e.target.value)}
                placeholder="如：客厅电脑、书房笔记本"
                maxLength={64}
                className="w-full px-4 py-3 rounded-xl bg-black/40 border border-cyan-500/30 text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-cyan-500/50 focus:border-cyan-500 text-sm"
              />
            </div>
            <button
              onClick={startConnect}
              className="w-full py-3 rounded-xl bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 font-medium hover:bg-cyan-500/30 transition-colors flex items-center justify-center gap-2"
            >
              <Zap className="w-5 h-5" />
              发起神经接驳
            </button>
            <button
              onClick={async () => {
                try {
                  await invoke("set_use_local_mode");
                  onPaired();
                } catch {
                  onPaired();
                }
              }}
              className="w-full py-2 text-sm text-white/40 hover:text-white/60 transition-colors"
            >
              使用本地 Key (跳过 L2)
            </button>
          </div>
        )}

        {(step === "connecting" || step === "waiting") && (
          <div className="flex flex-col items-center py-12 space-y-6">
            <div className="relative">
              <div className="w-20 h-20 rounded-full border-2 border-cyan-500/30 border-t-cyan-400 animate-spin" />
              <Loader2 className="w-12 h-12 text-cyan-400 absolute inset-0 m-auto animate-pulse" />
            </div>
            <p className="text-cyan-400/90 font-medium text-center">
              {step === "connecting" ? "正在向 L2 注册..." : "请求已发送，等待 L2 节点管理员审批..."}
            </p>
            <p className="text-white/40 text-xs text-center max-w-xs">
              管理员在 L2 后台将该节点分配给子账号后，引擎将自动点火
            </p>
          </div>
        )}

        {step === "success" && (
          <div className="flex flex-col items-center py-12 space-y-4">
            <div className="w-16 h-16 rounded-full bg-green-500/20 border-2 border-green-400/50 flex items-center justify-center">
              <Zap className="w-8 h-8 text-green-400" />
            </div>
            <p className="text-green-400 font-medium">神经接驳成功</p>
            <p className="text-white/50 text-sm">引擎已点火，进入控制台</p>
          </div>
        )}

        {step === "error" && (
          <div className="flex flex-col items-center py-12 space-y-4">
            <AlertCircle className="w-16 h-16 text-rose-400" />
            <p className="text-rose-400 text-sm text-center">{error}</p>
            <button
              onClick={() => { setStep("idle"); setError(""); }}
              className="px-6 py-2 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/30 transition-colors"
            >
              重试
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
