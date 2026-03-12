import { useState } from "react";
import ChatPanel from "./components/ChatPanel";
import StatusBar from "./components/StatusBar";
import DevicePanel from "./components/DevicePanel";
import SkillsPanel from "./components/SkillsPanel";
import VoiceTest from "./components/VoiceTest";
import { PerformanceDashboard } from "./components/Monitoring/PerformanceDashboard";
import { UISyncProvider } from "./components/UISyncProvider";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { useAppStore } from "./store/appStore";

function App() {
  const { isConnected } = useAppStore();

  return (
    <ErrorBoundary>
    <UISyncProvider>
    <div className="h-screen w-screen flex flex-col bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 text-white overflow-hidden">
      {/* 顶部状态栏 */}
      <StatusBar />

      {/* 主内容区 */}
      <div className="flex-1 flex gap-4 p-4 overflow-hidden">
        {/* 左侧：设备面板 */}
        <div className="w-80 flex-shrink-0">
          <DevicePanel />
        </div>

        {/* 中间：聊天面板 */}
        <div className="flex-1 flex flex-col min-w-0">
          <ChatPanel />
        </div>

        {/* 右侧：系统信息和语音测试面板 */}
        <div className="w-80 flex-shrink-0 hidden xl:block">
          <div className="h-full flex flex-col gap-4">
            {/* 系统信息 */}
            <div className="bg-slate-800/50 rounded-lg border border-purple-500/20 p-4">
              <h2 className="text-lg font-semibold mb-4">系统信息</h2>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-slate-400">连接状态:</span>
                  <span className={isConnected ? "text-green-400" : "text-red-400"}>
                    {isConnected ? "● 已连接" : "● 未连接"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">后端:</span>
                  <span className="text-purple-400">jachin-brain</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Dapr端口:</span>
                  <span className="text-purple-400">3500</span>
                </div>
              </div>
            </div>
            
            {/* 技能面板 */}
            <div className="flex-shrink-0 max-h-64 overflow-y-auto">
              <SkillsPanel />
            </div>

            {/* 语音测试 */}
            <div className="flex-1 overflow-y-auto bg-slate-800/50 rounded-lg border border-purple-500/20">
              <VoiceTest />
            </div>
            
            {/* 性能监控 */}
            <div className="mt-4 bg-slate-800/50 rounded-lg border border-purple-500/20 p-4">
              <h2 className="text-lg font-semibold mb-4">性能监控</h2>
              <PerformanceDashboard refreshInterval={10000} />
            </div>
          </div>
        </div>
      </div>
    </div>
    </UISyncProvider>
    </ErrorBoundary>
  );
}

export default App;
