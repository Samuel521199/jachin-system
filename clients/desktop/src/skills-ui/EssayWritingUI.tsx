/**
 * 写作文 · 生成式 UI：风格、字数、读者、语气、结构 + 主题输入
 * 与 Native 工具 core:compose_essay 参数对齐。
 */

import React, { useMemo, useState } from "react";
import type { SkillUiPanelProps } from "./types";

const STYLES = [
  { id: "narrative", label: "记叙文", hint: "写人记事、有场景" },
  { id: "argument", label: "议论文", hint: "论点论据论证" },
  { id: "expositive", label: "说明文", hint: "解释事物与条理" },
  { id: "practical", label: "应用文", hint: "书信、演讲稿等" },
  { id: "prose", label: "散文", hint: "抒情与联想" },
];

const WORD_COUNTS = [300, 500, 600, 800, 1200] as const;

const AUDIENCES = ["小学生", "初中生", "高中生", "大学生", "通用"];

const TONES = ["正式", "活泼", "抒情", "客观"];

const STRUCTURES = ["总-分-总", "起承转合", "并列式", "递进式"];

export const EssayWritingUI: React.FC<SkillUiPanelProps> = ({
  toolName,
  toolCallId,
  args,
  onToolResponse,
  layout = "inline",
}) => {
  const hintTopic = typeof args.topic === "string" ? args.topic : typeof args.topic_hint === "string" ? args.topic_hint : "";
  const [topic, setTopic] = useState(hintTopic);
  const [styleId, setStyleId] = useState(STYLES[0].id);
  const [wordCount, setWordCount] = useState<number>(600);
  const [audience, setAudience] = useState("通用");
  const [tone, setTone] = useState("正式");
  const [structure, setStructure] = useState("总-分-总");
  const [submitting, setSubmitting] = useState(false);

  const styleLabel = useMemo(() => STYLES.find((s) => s.id === styleId)?.label ?? "记叙文", [styleId]);

  const submit = async () => {
    if (submitting) return;
    setSubmitting(true);
    const t = topic.trim() || hintTopic.trim();
    try {
      await Promise.resolve(
        onToolResponse({
          topic: t || "（未命名主题）",
          style_id: styleId,
          style_label: styleLabel,
          word_count_target: wordCount,
          audience,
          tone,
          structure,
          tool: toolName,
          toolCallId,
        })
      );
    } catch {
      setSubmitting(false);
    }
  };

  const shellClass =
    layout === "canvas"
      ? "m-0 w-full max-w-none border-0 bg-transparent p-0 text-left shadow-none"
      : "rounded-xl border border-violet-500/30 bg-slate-900/65 p-3 text-left shadow-[0_0_24px_rgba(139,92,246,0.12)]";

  return (
    <div className={shellClass}>
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-violet-300/90">生成式 UI · 写作文</p>
      <p className="mb-3 text-xs text-slate-200/85">
        提交后通过 L3 WebSocket 执行 <span className="font-mono text-violet-200/80">core:compose_essay</span>
        ，{layout === "canvas" ? "回复写入左侧对应对话气泡。" : "回复写入本条气泡。"}
      </p>

      <label className="mb-2 block text-[11px] text-slate-400">主题</label>
      <input
        type="text"
        value={topic}
        readOnly={submitting}
        onChange={(e) => setTopic(e.target.value)}
        placeholder={hintTopic ? `默认：${hintTopic}` : "例如：难忘的暑假"}
        className={`mb-3 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-cyan-50 placeholder:text-slate-500 focus:border-violet-400/50 focus:outline-none read-only:opacity-70 ${layout === "canvas" ? "py-2.5" : ""}`}
      />

      <p className="mb-1.5 text-[11px] text-slate-400">文体</p>
      <div className="mb-3 flex flex-wrap gap-2">
        {STYLES.map((s) => (
          <button
            key={s.id}
            type="button"
            title={s.hint}
            disabled={submitting}
            onClick={() => setStyleId(s.id)}
            className={`rounded-lg border px-2.5 py-1.5 text-xs transition disabled:opacity-50 ${
              styleId === s.id
                ? "border-violet-400/60 bg-violet-500/20 text-violet-100"
                : "border-white/10 bg-white/5 text-slate-300 hover:border-white/20"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      <p className="mb-1.5 text-[11px] text-slate-400">目标字数（约）</p>
      <div className="mb-3 flex flex-wrap gap-2">
        {WORD_COUNTS.map((n) => (
          <button
            key={n}
            type="button"
            disabled={submitting}
            onClick={() => setWordCount(n)}
            className={`rounded-lg border px-2.5 py-1 text-xs disabled:opacity-50 ${
              wordCount === n
                ? "border-violet-400/60 bg-violet-500/20 text-violet-100"
                : "border-white/10 bg-white/5 text-slate-300 hover:border-white/20"
            }`}
          >
            {n} 字
          </button>
        ))}
      </div>

      <div
        className={`mb-3 grid grid-cols-1 gap-2 sm:grid-cols-3 ${layout === "canvas" ? "gap-3" : ""}`}
      >
        <div>
          <label className="mb-1 block text-[11px] text-slate-400">读者</label>
          <select
            value={audience}
            disabled={submitting}
            onChange={(e) => setAudience(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-slate-950/80 px-2 py-1.5 text-xs text-cyan-50 disabled:opacity-50"
          >
            {AUDIENCES.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-[11px] text-slate-400">语气</label>
          <select
            value={tone}
            disabled={submitting}
            onChange={(e) => setTone(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-slate-950/80 px-2 py-1.5 text-xs text-cyan-50 disabled:opacity-50"
          >
            {TONES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-[11px] text-slate-400">结构</label>
          <select
            value={structure}
            disabled={submitting}
            onChange={(e) => setStructure(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-slate-950/80 px-2 py-1.5 text-xs text-cyan-50 disabled:opacity-50"
          >
            {STRUCTURES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>

      <button
        type="button"
        disabled={submitting}
        onClick={() => void submit()}
        className="w-full rounded-lg bg-gradient-to-r from-violet-600/90 to-cyan-600/80 py-2.5 text-sm font-medium text-white shadow-lg hover:from-violet-500 hover:to-cyan-500/90 disabled:opacity-60"
      >
        {submitting ? "正在生成骨架…" : "确认并提交参数"}
      </button>
    </div>
  );
};
