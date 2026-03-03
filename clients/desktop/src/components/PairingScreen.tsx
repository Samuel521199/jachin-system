/**
 * Battle C: Scan-to-Connect pairing screen
 * Shows QR code + 6-digit fallback, polls until paired, writes config
 * Polish: Base URL config (gear), spawn daemon after success
 */

import { useState, useEffect, useCallback } from "react";
import { QRCodeSVG } from "qrcode.react";
import { invoke } from "@tauri-apps/api/core";
import { CheckCircle2, Loader2, Settings2 } from "lucide-react";

const POLL_INTERVAL_MS = 2000;
const DEFAULT_BASE = "http://localhost:3000";

function formatCode(code: string): string {
  if (code.length >= 6) return `${code.slice(0, 3)}-${code.slice(3, 6)}`;
  return code;
}

interface PairingScreenProps {
  onPaired: () => void;
}

export function PairingScreen({ onPaired }: PairingScreenProps) {
  const [step, setStep] = useState<"request" | "waiting" | "success" | "error">("request");
  const [shortCode, setShortCode] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [pairUrl, setPairUrl] = useState("");
  const [error, setError] = useState("");
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE);
  const [baseUrlLoaded, setBaseUrlLoaded] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsInput, setSettingsInput] = useState("");
  const [daemonSpawned, setDaemonSpawned] = useState(false);

  const loadBaseUrl = useCallback(async () => {
    try {
      const url = await invoke<string>("read_nexus_base_url");
      const u = url || DEFAULT_BASE;
      setBaseUrl(u);
      setSettingsInput(u);
    } catch {
      setBaseUrl(DEFAULT_BASE);
      setSettingsInput(DEFAULT_BASE);
    } finally {
      setBaseUrlLoaded(true);
    }
  }, []);

  useEffect(() => {
    loadBaseUrl();
  }, [loadBaseUrl]);

  const startPairing = useCallback(async (overrideUrl?: string) => {
    const url = overrideUrl ?? baseUrl;
    setStep("request");
    setError("");
    try {
      const res = await invoke<{
        session_id: string;
        short_code: string;
        pair_url: string;
      }>("pairing_request", { base_url: url });
      setSessionId(res.session_id);
      setShortCode(res.short_code);
      setPairUrl(res.pair_url);
      setStep("waiting");
    } catch (e) {
      setError(String(e));
      setStep("error");
    }
  }, [baseUrl]);

  const saveBaseUrl = useCallback(async () => {
    const url = settingsInput.trim() || DEFAULT_BASE;
    try {
      await invoke("write_nexus_base_url", { url });
      setBaseUrl(url);
      setShowSettings(false);
      if (step === "waiting" || step === "error") {
        startPairing(url);
      }
    } catch (e) {
      setError(String(e));
    }
  }, [settingsInput, step, startPairing]);

  useEffect(() => {
    if (baseUrlLoaded) startPairing();
  }, [baseUrlLoaded, startPairing]);

  useEffect(() => {
    if (step !== "waiting" || !sessionId) return;
    const id = setInterval(async () => {
      try {
        const status = await invoke<{
          status: string;
          access_token?: string;
          instance_id?: string;
          nexus_base_url?: string;
        }>("pairing_status", { session_id: sessionId, base_url: baseUrl });
        if (status.status === "success" && status.access_token && status.instance_id) {
          await invoke("write_nexus_config", {
            config: {
              instance_id: status.instance_id,
              access_token: status.access_token,
              nexus_base_url: status.nexus_base_url || baseUrl,
            },
          });
          setStep("success");
          try {
            await invoke("spawn_daemon");
            setDaemonSpawned(true);
          } catch {
            setDaemonSpawned(false);
          }
          setTimeout(() => onPaired(), 1500);
        } else if (status.status === "expired") {
          setError("Pairing code expired");
          setStep("error");
        }
      } catch {
        // ignore poll errors
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [step, sessionId, baseUrl, onPaired]);

  const qrContent = pairUrl && shortCode ? `${pairUrl}?code=${shortCode}` : "";

  return (
    <div className="h-screen w-screen flex flex-col items-center justify-center bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white overflow-hidden">
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(34, 211, 238, 0.08) 0%, transparent 60%),
            radial-gradient(ellipse 60% 40% at 50% 80%, rgba(139, 92, 246, 0.04) 0%, transparent 50%)
          `,
        }}
      />

      {/* Gear: Base URL config */}
      <button
        onClick={() => setShowSettings(!showSettings)}
        className="absolute top-6 right-6 z-20 p-2 rounded-lg text-white/40 hover:text-cyan-400 hover:bg-white/5 transition-colors"
        title="Custom Nexus Base URL"
      >
        <Settings2 className="w-5 h-5" />
      </button>

      {showSettings && (
        <div className="absolute top-16 right-6 z-20 w-72 rounded-xl border border-white/10 bg-slate-900/95 backdrop-blur p-4 shadow-xl">
          <p className="text-xs text-cyan-400/90 mb-2 uppercase tracking-wider">
            自定义指挥中枢 (Nexus Base URL)
          </p>
          <input
            type="text"
            value={settingsInput}
            onChange={(e) => setSettingsInput(e.target.value)}
            placeholder="http://localhost:3000"
            className="w-full px-3 py-2 rounded-lg bg-black/40 border border-white/20 text-white text-sm placeholder-white/30 focus:outline-none focus:ring-1 focus:ring-cyan-500 focus:border-cyan-500/50"
          />
          <div className="flex gap-2 mt-3">
            <button
              onClick={saveBaseUrl}
              className="flex-1 py-2 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 text-sm hover:bg-cyan-500/30 transition-colors"
            >
              Save
            </button>
            <button
              onClick={() => { setShowSettings(false); setSettingsInput(baseUrl); }}
              className="px-4 py-2 rounded-lg text-white/50 text-sm hover:bg-white/5 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="relative z-10 flex flex-col items-center max-w-md px-8">
        <h1 className="text-2xl font-bold text-cyan-400/95 mb-1 tracking-wide">
          Connect to Nexus
        </h1>
        <p className="text-sm text-white/50 mb-8">
          Scan to connect · 扫码即连
        </p>

        {step === "request" && (
          <div className="flex flex-col items-center py-8">
            <Loader2 className="w-12 h-12 text-cyan-400 animate-spin mb-4" />
            <p className="text-white/60 text-sm">Requesting pairing code...</p>
          </div>
        )}

        {step === "waiting" && qrContent && (
          <div className="flex flex-col items-center space-y-6">
            <div className="p-4 rounded-2xl bg-white">
              <QRCodeSVG value={qrContent} size={200} level="M" />
            </div>
            <p className="text-white/60 text-sm text-center">
              Or enter code: <span className="font-mono font-bold text-cyan-400">{formatCode(shortCode)}</span>
            </p>
            <p className="text-white/40 text-xs text-center max-w-xs">
              Open Nexus Console in browser, scan QR or enter the code above
            </p>
          </div>
        )}

        {step === "success" && (
          <div className="flex flex-col items-center py-8 space-y-4">
            <CheckCircle2 className="w-16 h-16 text-green-400" strokeWidth={1.5} />
            <p className="text-green-400 font-medium">神经链接已建立</p>
            <p className="text-white/50 text-sm">Neural link established</p>
            {daemonSpawned && (
              <p className="text-cyan-400/80 text-xs text-center max-w-xs">
                边缘引擎静默轰鸣中...
              </p>
            )}
          </div>
        )}

        {step === "error" && (
          <div className="flex flex-col items-center py-8 space-y-4">
            <p className="text-red-400 text-sm text-center">{error}</p>
            <p className="text-white/50 text-xs text-center">
              Ensure Nexus Console is running at {baseUrl}
            </p>
            <button
              onClick={startPairing}
              className="px-6 py-2 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/30 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {step === "request" && (
          <button
            onClick={startPairing}
            className="mt-8 px-6 py-2 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/30 transition-colors text-sm"
          >
            Start pairing
          </button>
        )}
      </div>
    </div>
  );
}
