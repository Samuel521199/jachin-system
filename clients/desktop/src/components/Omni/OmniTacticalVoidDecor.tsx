/**
 * 战术虚空层 — 径向景深、神经元呼吸、CRT 扫描、网格与底部角标；纯装饰，pointer-events-none
 */
import { motion } from "framer-motion";
import React from "react";

export function OmniTacticalVoidDecor() {
  return (
    <div className="pointer-events-none absolute inset-0 z-0 overflow-hidden">
      {/* 生命景深：非死黑 */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_85%_65%_at_50%_42%,rgb(15,23,42)_0%,rgb(0,0,0)_48%,rgb(0,0,0)_100%)]" />
      {/* 神经元呼吸 */}
      <motion.div
        className="absolute inset-0 bg-[radial-gradient(ellipse_70%_55%_at_50%_55%,rgba(6,182,212,0.14),transparent_62%)]"
        animate={{ opacity: [0.35, 0.72, 0.35] }}
        transition={{ duration: 8, repeat: Infinity, ease: [0.4, 0, 0.6, 1] }}
      />
      <div
        className="absolute inset-0 opacity-[0.32]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(6,182,212,0.07)_1px,transparent_1px),linear-gradient(90deg,rgba(6,182,212,0.07)_1px,transparent_1px)",
          backgroundSize: "28px 28px",
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.035]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        }}
      />
      {/* 水平 CRT 扫描感 */}
      <div
        className="absolute inset-0 mix-blend-overlay opacity-[0.05]"
        style={{
          backgroundImage:
            "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(200,230,255,0.9) 3px, rgba(200,230,255,0.9) 4px)",
        }}
      />
      <motion.div
        className="absolute inset-x-0 -top-1/2 h-[200%] w-full will-change-transform opacity-40 mix-blend-soft-light"
        style={{
          background:
            "repeating-linear-gradient(0deg, transparent 0px, transparent 4px, rgba(6,182,212,0.06) 4px, rgba(6,182,212,0.06) 5px)",
        }}
        animate={{ y: ["0%", "50%"] }}
        transition={{ duration: 7, repeat: Infinity, ease: "linear" }}
      />
      <div className="absolute bottom-3 left-3 h-9 w-9 border-b-2 border-l-2 border-cyan-400/45 shadow-[0_0_12px_rgba(34,211,238,0.3)]" />
      <div className="absolute bottom-3 right-3 h-9 w-9 border-b-2 border-r-2 border-cyan-400/45 shadow-[0_0_12px_rgba(34,211,238,0.3)]" />
    </div>
  );
}
