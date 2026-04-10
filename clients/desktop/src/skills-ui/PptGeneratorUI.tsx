/**
 * 演示用：PPT 模版选择面板（生成式 UI 样例）
 *
 * 约定 args 可选字段：
 * - templates: { id: string; label: string; description?: string }[]
 * - 若缺省，使用内置占位列表，便于本地联调。
 */

import React, { useMemo, useState } from "react";
import type { SkillUiPanelProps } from "./types";

const FALLBACK_TEMPLATES = [
  { id: "tech", label: "科技风", description: "深色背景、高对比强调线" },
  { id: "minimal", label: "极简风", description: "留白、无衬线标题" },
  { id: "biz", label: "商务风", description: "蓝白、图表友好" },
];

function parseTemplates(
  args: Record<string, unknown>
): { id: string; label: string; description?: string }[] {
  const raw = args.templates;
  if (!Array.isArray(raw) || raw.length === 0) return FALLBACK_TEMPLATES;
  const out: { id: string; label: string; description?: string }[] = [];
  for (const item of raw) {
    if (item && typeof item === "object") {
      const o = item as Record<string, unknown>;
      const id = typeof o.id === "string" ? o.id : typeof o.templateId === "string" ? o.templateId : "";
      const label = typeof o.label === "string" ? o.label : typeof o.name === "string" ? o.name : id;
      const description = typeof o.description === "string" ? o.description : undefined;
      if (id) out.push({ id, label: label || id, description });
    }
  }
  return out.length > 0 ? out : FALLBACK_TEMPLATES;
}

export const PptGeneratorUI: React.FC<SkillUiPanelProps> = ({
  toolName,
  toolCallId,
  args,
  onToolResponse,
  layout: _layout = "inline",
}) => {
  const templates = useMemo(() => parseTemplates(args), [args]);
  const [submitting, setSubmitting] = useState(false);

  const pick = async (t: { id: string; label: string }) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await Promise.resolve(
        onToolResponse({
          templateId: t.id,
          label: t.label,
          tool: toolName,
          toolCallId,
        })
      );
    } catch {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-xl border border-cyan-500/25 bg-slate-900/60 p-3 text-left shadow-[0_0_24px_rgba(34,211,238,0.08)]">
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-cyan-400/80">生成式 UI · PPT</p>
      <p className="mb-3 text-xs text-cyan-100/85">
        {submitting ? "正在提交到 L3…" : "选择模版后将通过 WebSocket 提交，回复写入本条气泡。"}
      </p>
      <ul className="flex flex-col gap-2">
        {templates.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              disabled={submitting}
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-left transition hover:border-cyan-400/40 hover:bg-cyan-500/10 disabled:opacity-50"
              onClick={() => void pick(t)}
            >
              <span className="block text-sm font-medium text-cyan-50">{t.label}</span>
              {t.description ? (
                <span className="mt-0.5 block text-[11px] text-cyan-200/50">{t.description}</span>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
};
