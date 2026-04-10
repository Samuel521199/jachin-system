/**
 * MermaidViewer — OMNI 暗色主题下将 ```mermaid 代码块渲染为 SVG；语法错误时隔离展示，不拖垮整页。
 *
 * 必须向 `mermaid.render(id, text, container)` 传入容器：否则 Mermaid 11 会把临时 div 挂到
 * `document.body`，解析失败时在 `removeTempElements` 之前抛错会留下「底部黑条 + Syntax error」残影。
 */
import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import mermaid from "mermaid";
import { toBlob } from "html-to-image";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { AlertTriangle, CheckCircle2, FileCode, ImageDown, Maximize, X, ZoomIn, ZoomOut } from "lucide-react";

function downloadTextFile(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.click();
  URL.revokeObjectURL(url);
}

/** 规范化 SVG 属性，提高 Canvas / 部分引擎的兼容性 */
function prepareSvgMarkupForRaster(svgMarkup: string): string {
  const parser = new DOMParser();
  const doc = parser.parseFromString(svgMarkup, "image/svg+xml");
  const svgEl = doc.documentElement;
  if (svgEl.querySelector("parsererror")) {
    return svgMarkup;
  }
  if (!svgEl.getAttribute("xmlns")) {
    svgEl.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  }
  let width = Number.parseFloat(String(svgEl.getAttribute("width") || "").replace(/px$/i, ""));
  let height = Number.parseFloat(String(svgEl.getAttribute("height") || "").replace(/px$/i, ""));
  const vb = svgEl.getAttribute("viewBox");
  if ((!width || !height) && vb) {
    const parts = vb.trim().split(/[\s,]+/).map(Number);
    if (parts.length >= 4 && parts[2] > 0 && parts[3] > 0) {
      width = parts[2];
      height = parts[3];
    }
  }
  if (width > 0 && height > 0) {
    svgEl.setAttribute("width", String(width));
    svgEl.setAttribute("height", String(height));
  }
  return new XMLSerializer().serializeToString(svgEl);
}

/** 回退：Canvas 栅格化（对含 foreignObject 的 Mermaid 图常失败，仅作兜底） */
async function svgMarkupToPngBlob(svgMarkup: string, scale = 2): Promise<Blob | null> {
  const prepared = prepareSvgMarkupForRaster(svgMarkup);
  const parser = new DOMParser();
  const doc = parser.parseFromString(prepared, "image/svg+xml");
  const svgEl = doc.documentElement;
  if (svgEl.querySelector("parsererror")) {
    return null;
  }
  let width = Number.parseFloat(svgEl.getAttribute("width") || "");
  let height = Number.parseFloat(svgEl.getAttribute("height") || "");
  const vb = svgEl.getAttribute("viewBox");
  if ((!width || !height) && vb) {
    const parts = vb.trim().split(/[\s,]+/).map(Number);
    if (parts.length >= 4 && parts[2] > 0 && parts[3] > 0) {
      width = parts[2];
      height = parts[3];
    }
  }
  if (!width || !height) {
    width = 960;
    height = 540;
  }
  const w = Math.max(1, Math.ceil(width * scale));
  const h = Math.max(1, Math.ceil(height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const img = new Image();
  const dataUrl =
    "data:image/svg+xml;charset=utf-8," + encodeURIComponent(prepared);
  return new Promise((resolve) => {
    img.onload = () => {
      try {
        ctx.drawImage(img, 0, 0, w, h);
        canvas.toBlob(
          (b) => {
            if (b && b.size > 0) {
              resolve(b);
              return;
            }
            try {
              const du = canvas.toDataURL("image/png");
              void fetch(du)
                .then((r) => r.blob())
                .then((fb) => resolve(fb && fb.size > 0 ? fb : null))
                .catch(() => resolve(null));
            } catch {
              resolve(null);
            }
          },
          "image/png",
          0.92
        );
      } catch {
        resolve(null);
      }
    };
    img.onerror = () => {
      resolve(null);
    };
    img.src = dataUrl;
  });
}

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
  /** 全屏预览内可见的 SVG 容器（有真实布局尺寸，避免离屏 0×0 导致 PNG 空文件） */
  const lightboxPngSourceRef = useRef<HTMLDivElement | null>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState(false);
  /** 导出结果提示（含保存位置说明） */
  const [exportNotice, setExportNotice] = useState<string | null>(null);

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

  useEffect(() => {
    if (!exportNotice) return;
    const t = window.setTimeout(() => setExportNotice(null), 5200);
    return () => window.clearTimeout(t);
  }, [exportNotice]);

  const notifyExportDone = useCallback(async (filename: string, formatLabel: "SVG" | "PNG") => {
    const pathHint =
      "文件已通过浏览器下载通道保存：一般在「下载」文件夹（路径多为 用户\\Downloads）；若改过默认保存位置，请到浏览器/系统设置的下载目录查看。";
    setExportNotice(`已保存 ${formatLabel}：${filename}。${pathHint}`);
    try {
      let granted = await isPermissionGranted().catch(() => false);
      if (!granted) {
        const r = await requestPermission().catch(() => "denied");
        granted = r === "granted";
      }
      if (granted) {
        sendNotification({
          title: "Jachin · 导出完成",
          body: `${filename}（${formatLabel}）已保存到默认下载目录`,
        });
      }
    } catch {
      /* 非 Tauri 或通知不可用时仅依赖页内提示 */
    }
  }, []);

  const openLb = useCallback(() => setLightbox(true), []);
  const closeLb = useCallback(() => setLightbox(false), []);

  const downloadSvgFile = useCallback(() => {
    if (!svg) return;
    const name = `jachin-mermaid-${Date.now()}.svg`;
    downloadTextFile(name, svg, "image/svg+xml;charset=utf-8");
    void notifyExportDone(name, "SVG");
  }, [svg, notifyExportDone]);

  const downloadPngFile = useCallback(async () => {
    if (!svg) return;
    const name = `jachin-mermaid-${Date.now()}.png`;
    let blob: Blob | null = null;

    const node = lightboxPngSourceRef.current;
    if (node?.querySelector("svg")) {
      try {
        try {
          await document.fonts.ready;
        } catch {
          /* ignore */
        }
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
        blob = await toBlob(node, {
          pixelRatio: 2,
          backgroundColor: "#0f172a",
          cacheBust: true,
        });
        if (blob && blob.size === 0) {
          blob = null;
        }
      } catch {
        blob = null;
      }
    }

    if (!blob) {
      blob = await svgMarkupToPngBlob(svg, 2);
    }
    if (blob && blob.size === 0) {
      blob = null;
    }

    if (!blob) {
      setExportNotice("PNG 导出失败（可改用「下载 SVG」）。请先全屏预览后再点 PNG。");
      return;
    }

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.rel = "noopener";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    window.setTimeout(() => {
      a.remove();
      URL.revokeObjectURL(url);
    }, 800);
    void notifyExportDone(name, "PNG");
  }, [svg, notifyExportDone]);

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
                      ref={lightboxPngSourceRef}
                      className="flex h-full w-full min-h-0 min-w-0 items-center justify-center p-4 [&_svg]:!box-content [&_svg]:!h-auto [&_svg]:!max-h-none [&_svg]:!max-w-none [&_svg]:!min-h-0 [&_svg]:!min-w-0 [&_svg]:!w-auto"
                      // eslint-disable-next-line react/no-danger
                      dangerouslySetInnerHTML={{ __html: svg }}
                    />
                  </TransformComponent>
                  <div
                    className="pointer-events-auto absolute bottom-8 left-1/2 z-20 flex max-w-[calc(100%-2rem)] -translate-x-1/2 flex-wrap items-center justify-center gap-0.5 rounded-full border border-white/10 bg-slate-800/80 px-1.5 py-1 shadow-lg backdrop-blur-sm"
                    role="toolbar"
                    aria-label="图表缩放、导出与关闭"
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
                    <span className="mx-0.5 h-6 w-px shrink-0 bg-white/15" aria-hidden />
                    <button
                      type="button"
                      className="rounded-lg p-2 text-slate-200 transition-colors hover:bg-cyan-500/20 hover:text-cyan-100"
                      title="下载 SVG"
                      aria-label="下载 SVG"
                      onClick={(e) => {
                        e.stopPropagation();
                        downloadSvgFile();
                      }}
                    >
                      <FileCode className="h-5 w-5" aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-slate-200 transition-colors hover:bg-cyan-500/20 hover:text-cyan-100"
                      title="下载 PNG 图片"
                      aria-label="下载 PNG 图片"
                      onClick={(e) => {
                        e.stopPropagation();
                        void downloadPngFile();
                      }}
                    >
                      <ImageDown className="h-5 w-5" aria-hidden />
                    </button>
                    <span className="mx-0.5 h-6 w-px shrink-0 bg-white/15" aria-hidden />
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
    <div className="relative my-2 w-full min-w-0 max-w-full">
      {sandbox}
      {body}
      {exportNotice && (
        <div
          className={
            exportNotice.includes("失败")
              ? "pointer-events-none fixed left-1/2 top-[max(1rem,env(safe-area-inset-top))] z-[100002] w-[min(92vw,24rem)] -translate-x-1/2 rounded-lg border border-rose-500/45 bg-rose-950/95 px-3 py-2.5 text-xs leading-relaxed text-rose-50 shadow-xl backdrop-blur-md"
              : "pointer-events-none fixed left-1/2 top-[max(1rem,env(safe-area-inset-top))] z-[100002] w-[min(92vw,24rem)] -translate-x-1/2 rounded-lg border border-emerald-500/40 bg-emerald-950/95 px-3 py-2.5 text-xs leading-relaxed text-emerald-50 shadow-xl backdrop-blur-md"
          }
          role="status"
          aria-live="polite"
        >
          <div className="flex gap-2">
            {exportNotice.includes("失败") ? (
              <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" aria-hidden />
            ) : (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" aria-hidden />
            )}
            <span>{exportNotice}</span>
          </div>
        </div>
      )}
    </div>
  );
}
