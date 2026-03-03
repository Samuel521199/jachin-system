"use client";

const PALETTE_ITEMS = [
  { type: "trigger" as const, label: "麦克风语音唤醒", pluginId: "geek-a-wake", price: "$1" },
  { type: "trigger" as const, label: "HTTP Webhook", pluginId: "geek-b-webhook", price: "$2" },
  { type: "processor" as const, label: "本地离线 LLM", pluginId: "geek-c-llm", price: "$5" },
  { type: "processor" as const, label: "情感分析 WASM", pluginId: "geek-d-wasm", price: "$3" },
  { type: "action" as const, label: "扬声器播放", pluginId: "geek-e-tts", price: "$2" },
  { type: "action" as const, label: "控制 IoT 继电器", pluginId: "geek-f-iot", price: "$4" },
];

export function NodePalette() {
  const onDragStart = (e: React.DragEvent, item: (typeof PALETTE_ITEMS)[0]) => {
    e.dataTransfer.setData("application/reactflow", JSON.stringify(item));
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="w-56 shrink-0 p-4">
      <div className="sticky top-24 rounded-2xl backdrop-blur-md bg-white/5 border border-white/10 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-widest text-cyan-400/90 mb-2">
          神经元组件库
        </h3>
        <p className="text-[10px] text-white/40 mb-4">拖拽到画布放置</p>
        <div className="space-y-2">
          {PALETTE_ITEMS.map((item) => (
            <div
              key={`${item.type}-${item.pluginId}`}
              draggable
              onDragStart={(e) => onDragStart(e, item)}
              className="
                flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg
                bg-black/40 border border-white/10
                cursor-grab active:cursor-grabbing
                hover:bg-white/10 hover:border-cyan-500/30 hover:shadow-[0_0_15px_rgba(34,211,238,0.15)]
                transition-all select-none
              "
            >
              <span className="text-sm font-mono text-white/90 truncate">{item.label}</span>
              <span className="text-[10px] text-cyan-400/80 font-mono shrink-0">{item.price}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export { PALETTE_ITEMS };
