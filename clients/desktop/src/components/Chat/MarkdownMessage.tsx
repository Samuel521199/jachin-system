/**
 * MarkdownMessage - 将 Markdown 文本渲染为格式化的 HTML
 * 用于 HR 透析镜等技能输出的 # 标题、**粗体**、表格等正确展示
 */
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const components = {
  h1: ({ children }) => <h1 className="text-lg font-semibold text-cyan-100 mt-3 mb-2 first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="text-base font-semibold text-cyan-100 mt-3 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-semibold text-cyan-100/90 mt-2 mb-1">{children}</h3>,
  p: ({ children }) => <p className="text-slate-200 leading-relaxed my-2">{children}</p>,
  strong: ({ children }) => <strong className="text-cyan-200 font-semibold">{children}</strong>,
  ul: ({ children }) => <ul className="list-disc list-inside my-2 space-y-0.5 text-slate-200">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside my-2 space-y-0.5 text-slate-200">{children}</ol>,
  li: ({ children }) => <li className="text-slate-200">{children}</li>,
  table: ({ children }) => (
    <div className="overflow-x-auto my-3">
      <table className="w-full text-sm border-collapse border border-white/20">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-white/10">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="border-b border-white/20">{children}</tr>,
  th: ({ children }) => (
    <th className="border border-white/20 px-2 py-1.5 text-left text-cyan-100 font-medium">{children}</th>
  ),
  td: ({ children }) => <td className="border border-white/20 px-2 py-1.5 text-slate-200">{children}</td>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-cyan-400/70 bg-white/5 py-0.5 pl-3 my-2 text-slate-300 italic">
      {children}
    </blockquote>
  ),
  code: ({ children }) => (
    <code className="text-cyan-300 bg-white/10 px-1 rounded text-xs font-mono">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="bg-white/10 border border-white/20 rounded-lg p-3 my-2 overflow-x-auto text-sm text-slate-200">
      {children}
    </pre>
  ),
  hr: () => <hr className="border-white/20 my-3" />,
};

export interface MarkdownMessageProps {
  content: string;
  className?: string;
}

export function MarkdownMessage({ content, className = "" }: MarkdownMessageProps) {
  if (!content || typeof content !== "string") return null;
  return (
    <div className={`markdown-content text-sm ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
