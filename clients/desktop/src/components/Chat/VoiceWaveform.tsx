/**
 * 语音态声波动画：倾听用实时电平，思考/播报用程序化流光（无麦克风时）
 */

import React, { useEffect, useRef } from "react";

export type WavePhase = "mic_listen" | "thinking" | "speaking";

export interface VoiceWaveformProps {
  phase: WavePhase;
  /** 0–1，来自 Web Audio Analyser（仅 mic_listen 有效） */
  micLevel?: number;
  className?: string;
}

const BARS = 40;

export const VoiceWaveform: React.FC<VoiceWaveformProps> = ({
  phase,
  micLevel = 0,
  className = "",
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tRef = useRef(0);
  const rafRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const draw = (now: number) => {
      tRef.current = now * 0.001;
      const t = tRef.current;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w < 2 || h < 2) {
        rafRef.current = requestAnimationFrame(draw);
        return;
      }
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const mid = h * 0.55;
      const barW = Math.max(2, (w - 16) / BARS - 2);

      if (phase === "thinking") {
        const grad = ctx.createLinearGradient(0, mid - 4, w, mid + 4);
        const shift = (t * 80) % (w + 40);
        grad.addColorStop(0, "rgba(34,211,238,0)");
        grad.addColorStop(0.35, "rgba(34,211,238,0.15)");
        grad.addColorStop(0.5, "rgba(167,139,250,0.85)");
        grad.addColorStop(0.65, "rgba(34,211,238,0.15)");
        grad.addColorStop(1, "rgba(34,211,238,0)");
        ctx.fillStyle = grad;
        ctx.beginPath();
        let first = true;
        for (let x = -40; x < w + 40; x += 4) {
          const y = mid + Math.sin((x + shift) * 0.04 + t * 2) * 3;
          if (first) {
            ctx.moveTo(x, y);
            first = false;
          } else {
            ctx.lineTo(x, y);
          }
        }
        ctx.lineTo(w + 40, h);
        ctx.lineTo(-40, h);
        ctx.closePath();
        ctx.fill();
        rafRef.current = requestAnimationFrame(draw);
        return;
      }

      for (let i = 0; i < BARS; i++) {
        const x = 8 + i * (barW + 2);
        let amp: number;
        if (phase === "mic_listen") {
          const jitter = Math.sin(t * 14 + i * 0.35) * 0.06;
          const base = micLevel * (0.55 + 0.45 * Math.sin(t * 8 + i * 0.5));
          amp = Math.min(1, Math.max(0.08, base + jitter));
        } else {
          amp =
            0.2 +
            0.55 *
              Math.abs(Math.sin(t * 10 + i * 0.4)) *
              (0.5 + 0.5 * Math.sin(t * 3 + i));
        }
        const bh = Math.max(4, amp * (h * 0.72));
        const y = mid - bh / 2;
        const cyan = phase === "speaking" ? "rgba(167,139,250," : "rgba(34,211,238,";
        const alpha = 0.35 + amp * 0.55;
        ctx.fillStyle = `${cyan}${alpha.toFixed(2)})`;
        ctx.fillRect(x, y, barW, bh);
      }

      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, [phase, micLevel]);

  return (
    <div
      className={`flex items-center justify-center min-h-[44px] w-full ${className}`}
      aria-hidden
    >
      <canvas ref={canvasRef} className="w-full h-11 block" />
    </div>
  );
};

export default VoiceWaveform;
