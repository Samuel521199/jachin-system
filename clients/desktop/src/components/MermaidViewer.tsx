/**
 * MermaidViewer — OMNI 暗色主题下将 ```mermaid 代码块渲染为 SVG；语法错误时隔离展示，不拖垮整页。
 *
 * 必须向 `mermaid.render(id, text, container)` 传入容器：否则 Mermaid 11 会把临时 div 挂到
 * `document.body`，解析失败时在 `removeTempElements` 之前抛错会留下「底部黑条 + Syntax error」残影。
 */
import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import mermaid from "mermaid";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";
import { AlertTriangle, Maximize, X, ZoomIn, ZoomOut } from "lucide-react";

let mermaidInitialized = false;

function ensureMermaidInit() {
  if (mermaidInitialized) return;
  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    securityLevel: "loose",
    /** 不在内部再画错误示意图，避免与自定义 fallback 重复，且利于配合容器清理 */
    suppressErrorRendering: true,
    themeVariables: {
      darkMode: true,
      background: "transparent",
      mainBkg: "transparent",
      secondaryColor: "#334155",
      primaryTextColor: "#e2e8f0",
      lineColor: "#94a3b8",
    },
  });
  mermaidInitialized = true;
}

export interface MermaidViewerProps {
  /** mermaid 源码（不含外层 ```） */
  code: string;
}

export function MermaidViewer({ code }: MermaidViewerProps) {
  const reactId = useId().replace(/:/g, "");
  const sandboxRef = useRef<HTMLDivElement | null>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setErr(null);

    const run = async () => {
      const host = sandboxRef.current;
      if (!host) return;
      try {
        ensureMermaidInit();
        const id = `mmd-${reactId}-${Date.now().toString(36)}`;
        host.innerHTML = "";
        const { svg: out } = await mermaid.render(id, code.trim(), host);
        if (!cancelled) setSvg(out);
      } catch (e) {
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e);
          setErr(msg);
        }
      } finally {
        if (sandboxRef.current) {
          sandboxRef.current.innerHTML = "";
        }
      }
    };

    void run();
    return () => {
      cancelled = true;
      if (sandboxRef.current) {
        sandboxRef.current.innerHTML = "";
      }
    };
  }, [code, reactId]);

  const openLb = useCallback(() => setLightbox(true), []);
  const closeLb = useCallback(() => setLightbox(false), []);

  const sandbox = (
    <div
      ref={sandboxRef}
      className="pointer-events-none fixed left-0 top-0 h-px w-px overflow-hidden opacity-0"
      aria-hidden
    />
  );

  let body: React.ReactNode;
  if (err) {
    body = (
      <div className="space-y-2" role="alert">
        <div className="rounded-lg border border-amber-500/45 bg-amber-950/35 px-3 py-2 text-sm text-amber-100 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-400" aria-hidden />
          <div className="min-w-0 flex-1">
            <div className="font-medium text-amber-50">图表未渲染（Mermaid 语法或版本不兼容）</div>
            <div className="mt-1 text-xs text-amber-200/85 font-mono break-all">{err}</div>
          </div>
        </div>
        <p className="text-xs text-slate-400">
          以下为源码，可对照修改或复制到{" "}
          <a
            href="https://mermaid.live"
            target="_blank"
            rel="noopener noreferrer"
            className="text-cyan-400/90 underline underline-offset-2 hover:text-cyan-300"
          >
            mermaid.live
          </a>{" "}
          调试。
        </p>
        <pre className="max-h-80 overflow-auto rounded-lg border border-white/15 bg-black/45 p-3 text-xs leading-relaxed text-slate-200 font-mono whitespace-pre-wrap break-words">
          {code.trim()}
        </pre>
      </div>
    );
  } else if (!svg) {
    body = (
      <div className="h-24 rounded-lg border border-white/15 bg-white/5 animate-pulse text-xs text-slate-500 flex items-center justify-center">
        图表渲染中…
      </div>
    );
  } else {
    body = (
      <>
      <button
        type="button"
        className="group relative w-full max-w-full cursor-pointer rounded-lg border border-white/15 bg-transparent text-left outline-none ring-cyan-400/50 focus-visible:ring-2"
        onClick={openLb}
        aria-label="放大查看 Mermaid 图表"
      >
        <div
          className="max-h-64 overflow-auto px-2 py-2 [&_svg]:max-w-full [&_svg]:h-auto"
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: svg }}
        />
        <span className="pointer-events-none absolute bottom-1 right-1 rounded bg-black/50 px-1.5 py-0.5 text-[10px] text-slate-400 opacity-0 transition-opacity group-hover:opacity-100">
          点击放大
        </span>
      </button>

      {lightbox && (
        <div
          className="fixed inset-0 z-[100000] flex items-center justify-center bg-black/85 p-6 sm:p-10 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="Mermaid 全屏预览"
          onClick={closeLb}
        >
          {/* 仅遮罩层（padding 外缘）可点穿关闭；内容区 stopPropagation */}
          <div
            className="relative box-border flex h-[min(92vh,calc(100%-3rem))] w-[min(96vw,calc(100%-3rem))] min-h-[240px] flex-col overflow-hidden rounded-lg border border-white/20 bg-slate-950/95 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <TransformWrapper
              initialScale={1}
              minScale={0.5}
              maxScale={8}
              centerOnInit={true}
              wheel={{ step: 0.1 }}
              limitToBounds={false}
            >
              {({ zoomIn, zoomOut, resetTransform }) => (
                <div className="relative flex h-full min-h-0 w-full flex-1 flex-col">
                  <TransformComponent
                    wrapperClass="!h-full !min-h-0 !w-full !flex-1"
                    contentClass="!flex !h-full !min-h-0 !w-full !items-center !justify-center"
                    wrapperStyle={{ width: "100%", height: "100%", minHeight: 0, flex: 1 }}
                  >
                    <div
                      className="flex h-full w-full min-h-0 min-w-0 items-center justify-center p-4 [&_svg]:!box-content [&_svg]:!h-auto [&_svg]:!max-h-none [&_svg]:!max-w-none [&_svg]:!min-h-0 [&_svg]:!min-w-0 [&_svg]:!w-auto"
                      // eslint-disable-next-line react/no-danger
                      dangerouslySetInnerHTML={{ __html: svg }}
                    />
                  </TransformComponent>
                  <div
                    className="pointer-events-auto absolute bottom-8 left-1/2 z-20 flex -translate-x-1/2 items-center gap-0.5 rounded-full border border-white/10 bg-slate-800/80 px-1.5 py-1 shadow-lg backdrop-blur-sm"
                    role="toolbar"
                    aria-label="图表缩放与关闭"
                  >
                    <button
                      type="button"
                      className="rounded-lg p-2 text-slate-200 transition-colors hover:bg-white/10"
                      aria-label="放大"
                      onClick={() => zoomIn()}
                    >
                      <ZoomIn className="h-5 w-5" aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-slate-200 transition-colors hover:bg-white/10"
                      aria-label="缩小"
                      onClick={() => zoomOut()}
                    >
                      <ZoomOut className="h-5 w-5" aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-slate-200 transition-colors hover:bg-white/10"
                      aria-label="还原视图"
                      onClick={() => resetTransform()}
                    >
                      <Maximize className="h-5 w-5" aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-slate-200 transition-colors hover:bg-red-500/20 hover:text-red-200"
                      aria-label="关闭"
                      onClick={closeLb}
                    >
                      <X className="h-5 w-5" aria-hidden />
                    </button>
                  </div>
                </div>
              )}
            </TransformWrapper>
          </div>
        </div>
      )}
    </>
    );
  }

  return (
    <div className="my-2 w-full min-w-0 max-w-full">
      {sandbox}
      {body}
    </div>
  );
}
