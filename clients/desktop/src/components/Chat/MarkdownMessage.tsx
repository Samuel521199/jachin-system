/**
 * MarkdownMessage - 将 Markdown 文本渲染为格式化的 HTML
 * 用于 HR 透析镜等技能输出的 # 标题、**粗体**、表格等正确展示
 */
import React, { type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MermaidViewer } from "../MermaidViewer";

interface ChildProps { children?: ReactNode }

interface CodeProps extends ChildProps {
  className?: string;
  /** react-markdown：行内代码为 true，围栏代码块为 false */
  inline?: boolean;
}

const components = {
  h1: ({ children }: ChildProps) => <h1 className="text-lg font-semibold text-cyan-100 mt-3 mb-2 first:mt-0">{children}</h1>,
  h2: ({ children }: ChildProps) => <h2 className="text-base font-semibold text-cyan-100 mt-3 mb-2">{children}</h2>,
  h3: ({ children }: ChildProps) => <h3 className="text-sm font-semibold text-cyan-100/90 mt-2 mb-1">{children}</h3>,
  p: ({ children }: ChildProps) => <p className="text-slate-200 leading-relaxed my-2">{children}</p>,
  strong: ({ children }: ChildProps) => <strong className="text-cyan-200 font-semibold">{children}</strong>,
  ul: ({ children }: ChildProps) => <ul className="list-disc list-inside my-2 space-y-0.5 text-slate-200">{children}</ul>,
  ol: ({ children }: ChildProps) => <ol className="list-decimal list-inside my-2 space-y-0.5 text-slate-200">{children}</ol>,
  li: ({ children }: ChildProps) => <li className="text-slate-200">{children}</li>,
  table: ({ children }: ChildProps) => (
    <div className="overflow-x-auto my-3">
      <table className="w-full text-sm border-collapse border border-white/20">{children}</table>
    </div>
  ),
  thead: ({ children }: ChildProps) => <thead className="bg-white/10">{children}</thead>,
  tbody: ({ children }: ChildProps) => <tbody>{children}</tbody>,
  tr: ({ children }: ChildProps) => <tr className="border-b border-white/20">{children}</tr>,
  th: ({ children }: ChildProps) => (
    <th className="border border-white/20 px-2 py-1.5 text-left text-cyan-100 font-medium">{children}</th>
  ),
  td: ({ children }: ChildProps) => <td className="border border-white/20 px-2 py-1.5 text-slate-200">{children}</td>,
  blockquote: ({ children }: ChildProps) => (
    <blockquote className="border-l-2 border-cyan-400/70 bg-white/5 py-0.5 pl-3 my-2 text-slate-300 italic">
      {children}
    </blockquote>
  ),
  code: ({ children, className, inline }: CodeProps) => {
    const isMermaidBlock =
      /\blanguage-mermaid\b/.test(className || "") && inline !== true;
    if (isMermaidBlock) {
      const raw = String(children ?? "").replace(/\n$/, "");
      return <MermaidViewer code={raw} />;
    }
    if (inline) {
      return (
        <code className="text-cyan-300 bg-white/10 px-1 rounded text-xs font-mono">{children}</code>
      );
    }
    return (
      <code className={className}>
        {children}
      </code>
    );
  },
  pre: ({ children }: ChildProps) => {
    const arr = React.Children.toArray(children);
    if (
      arr.length === 1 &&
      React.isValidElement(arr[0]) &&
      arr[0].type === MermaidViewer
    ) {
      return <>{children}</>;
    }
    return (
      <pre className="bg-white/10 border border-white/20 rounded-lg p-3 my-2 overflow-x-auto text-sm text-slate-200">
        {children}
      </pre>
    );
  },
  hr: () => <hr className="border-white/20 my-3" />,
};

export interface MarkdownMessageProps {
  content: string;
  className?: string;
}

export function MarkdownMessage({ content, className = "" }: MarkdownMessageProps) {
  if (!content || typeof content !== "string") return null;
  return (
    <div
      className={`markdown-content min-w-0 max-w-full overflow-x-hidden text-sm break-words [overflow-wrap:anywhere] ${className}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
