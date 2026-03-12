import { useState, useEffect } from "react";
import { Cpu, Wifi, WifiOff, Activity } from "lucide-react";
import { getDevices, DeviceStatus } from "../lib/api";

export default function DevicePanel() {
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const loadDevices = async () => {
      try {
        const deviceList = await getDevices();
        setDevices(deviceList);
      } catch (error) {
        console.error("Failed to load devices:", error);
      } finally {
        setIsLoading(false);
      }
    };

    loadDevices();
    const interval = setInterval(loadDevices, 10000); // 每10秒刷新
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="h-full bg-slate-800/50 rounded-lg border border-purple-500/20 p-4 overflow-y-auto">
      <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
        <Activity className="w-5 h-5" />
        设备列表
      </h2>

      {isLoading ? (
        <div className="text-center text-slate-400 py-8">加载中...</div>
      ) : devices.length === 0 ? (
        <div className="text-center text-slate-400 py-8">
          <Cpu className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>暂无设备</p>
          <p className="text-sm mt-2 text-slate-500">
            设备连接后将显示在这里
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {devices.map((device) => (
            <div
              key={device.deviceId}
              className="bg-slate-700/50 rounded-lg p-3 border border-purple-500/10 hover:border-purple-500/30 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-medium">{device.name}</h3>
                {device.status === "online" ? (
                  <Wifi className="w-4 h-4 text-green-400" />
                ) : (
                  <WifiOff className="w-4 h-4 text-red-400" />
                )}
              </div>
              <div className="text-xs text-slate-400">
                <div>ID: {device.deviceId}</div>
                <div className="mt-1">
                  能力: {device.capabilities.join(", ") || "无"}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
