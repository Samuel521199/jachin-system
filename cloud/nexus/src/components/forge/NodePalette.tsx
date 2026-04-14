"use client";

import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import { nexusForge } from "@/lib/nexus-ui-i18n";

export function NodePalette() {
  const { lang } = useNexusUiLang();
  const t = nexusForge[lang];
  const paletteItems = t.palette;

  const onDragStart = (e: React.DragEvent, item: (typeof paletteItems)[number]) => {
    e.dataTransfer.setData("application/reactflow", JSON.stringify(item));
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="w-56 shrink-0 p-4">
      <div className="sticky top-24 rounded-2xl backdrop-blur-md bg-white/5 border border-white/10 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-widest text-cyan-400/90 mb-2">
          {t.paletteTitle}
        </h3>
        <p className="text-[10px] text-white/40 mb-4">{t.paletteHint}</p>
        <div className="space-y-2">
          {paletteItems.map((item) => (
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
