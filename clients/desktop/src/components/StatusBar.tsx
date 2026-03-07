import { useEffect } from "react";
import { Loader2 } from "lucide-react";
import { useAppStore } from "../store/appStore";
import { checkHealth } from "../lib/api";
import { useSkillSync } from "../hooks/useSkillSync";

export default function StatusBar() {
  const { isConnected, setConnected } = useAppStore();
  const { syncing, progress } = useSkillSync();

  useEffect(() => {
    let mounted = true;

    const checkConnection = async () => {
      try {
        await checkHealth();
        if (mounted) {
          setConnected(true);
        }
      } catch (error) {
        if (mounted) {
          setConnected(false);
        }
      }
    };

    // 立即检查一次
    checkConnection();

    // 定期检查连接状态（每5秒）
    const interval = setInterval(checkConnection, 5000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [setConnected]);

  return (
    <div className="h-12 bg-slate-800/80 border-b border-purple-500/20 flex items-center justify-between px-6">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
          Jachin Console
        </h1>
        <div className="flex items-center gap-2 text-sm">
          {syncing && (
            <span className="flex items-center gap-1.5 text-amber-400 text-xs">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              正在同步企业资产...
            </span>
          )}
          <div
            className={`w-2 h-2 rounded-full ${
              isConnected ? "bg-green-400 animate-pulse" : "bg-red-400"
            }`}
          />
          <span className={isConnected ? "text-green-400" : "text-red-400"}>
            {isConnected ? "已连接" : "未连接"}
          </span>
        </div>
      </div>
      <div className="text-sm text-slate-400">
        v0.1.0 | Dapr: localhost:3500
      </div>
    </div>
  );
}
