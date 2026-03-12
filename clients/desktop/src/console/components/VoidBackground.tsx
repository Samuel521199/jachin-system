/**
 * VoidBackground - 背景星空/拓扑层 (The Void)
 * 设计愿景 2.1：Canvas 绘制低透明度、缓慢漂移的节点，象征记忆点/已连接设备；与记忆/设备数绑定
 * 阶段 6.4：节点位置与设备/记忆数据绑定，记忆节点与设备节点区分渲染
 */

import { useRef, useEffect, useMemo } from "react";
import { cn } from "../../utils/cn";

const DEFAULT_NODE_COUNT = 36;
const SEED = 42;

function seeded(i: number) {
  const x = Math.sin(SEED + i * 1.5) * 10000;
  return x - Math.floor(x);
}

type NodeType = "memory" | "device" | "default";

export function VoidBackground({
  className,
  nodeCount,
  memoryCount,
  deviceCount,
}: {
  className?: string;
  /** 可选：总节点数，不传则使用 memoryCount+deviceCount 或默认 36；范围 8～60 */
  nodeCount?: number;
  /** 可选：记忆数量，与 deviceCount 一起传入时区分渲染 */
  memoryCount?: number;
  /** 可选：设备数量，与 memoryCount 一起传入时区分渲染 */
  deviceCount?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const { nodes } = useMemo(() => {
    const mem = Math.max(0, Math.round(memoryCount ?? 0));
    const dev = Math.max(0, Math.round(deviceCount ?? 0));
    const hasSplit = mem > 0 || dev > 0;
    const totalFromData = Math.min(60, Math.max(8, mem + dev || DEFAULT_NODE_COUNT));
    const count = nodeCount != null
      ? Math.min(60, Math.max(8, Math.round(nodeCount)))
      : totalFromData;

    const list: Array<{
      x: number; y: number; size: number; phase: number; speed: number;
      amplitude: number; opacity: number; type: NodeType;
    }> = [];
    for (let i = 0; i < count; i++) {
      const type: NodeType = hasSplit
        ? i < mem ? "memory" : "device"
        : "default";
      list.push({
        x: 5 + seeded(i) * 90,
        y: 5 + seeded(i + 10) * 90,
        size: 1 + Math.floor(seeded(i + 20) * 2),
        phase: seeded(i + 30) * Math.PI * 2,
        speed: 0.3 + seeded(i + 40) * 0.2,
        amplitude: 2 + seeded(i + 50) * 3,
        opacity: 0.15 + seeded(i + 60) * 0.2,
        type,
      });
    }
    return { nodes: list };
  }, [nodeCount, memoryCount, deviceCount]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      ctx.scale(dpr, dpr);
    };
    resize();
    window.addEventListener("resize", resize);

    let t = 0;
    let rafId: number;

    const loop = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      ctx.clearRect(0, 0, w, h);

      nodes.forEach((node) => {
        const yOffset = Math.sin(t * node.speed + node.phase) * node.amplitude;
        const x = (node.x / 100) * w;
        const y = (node.y / 100) * h + yOffset;
        const r = node.size;

        let fillStyle: string;
        if (node.type === "memory") {
          fillStyle = `rgba(100,180,255,${node.opacity})`; // 记忆：淡蓝
        } else if (node.type === "device") {
          fillStyle = `rgba(100,255,150,${node.opacity})`; // 设备：淡绿
        } else {
          fillStyle = `rgba(255,255,255,${node.opacity})`; // 默认：白
        }

        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = fillStyle;
        ctx.fill();
      });

      t += 0.02;
      rafId = requestAnimationFrame(loop);
    };

    rafId = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
    };
  }, [nodes]);

  return (
    <canvas
      ref={canvasRef}
      className={cn(
        "pointer-events-none absolute inset-0 z-0 overflow-hidden",
        className
      )}
      aria-hidden
    />
  );
}
