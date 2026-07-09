import { motion } from "framer-motion";
import React from "react";

export function OmniTacticalVoidDecor() {
  return (
    <div className="pointer-events-none absolute inset-0 z-0 overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_95%_70%_at_50%_18%,rgba(14,30,48,0.92)_0%,rgba(5,8,13,0.96)_52%,rgba(2,4,8,0.98)_100%)]" />
      <motion.div
        className="absolute inset-0 bg-[radial-gradient(ellipse_62%_45%_at_50%_60%,rgba(56,189,248,0.08),transparent_68%)]"
        animate={{ opacity: [0.24, 0.46, 0.24] }}
        transition={{ duration: 10, repeat: Infinity, ease: [0.4, 0, 0.6, 1] }}
      />
      <div
        className="absolute inset-0 opacity-[0.16]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(125,211,252,0.04)_1px,transparent_1px),linear-gradient(90deg,rgba(125,211,252,0.035)_1px,transparent_1px)",
          backgroundSize: "36px 36px",
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.025]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        }}
      />
      <div
        className="absolute inset-0 mix-blend-overlay opacity-[0.025]"
        style={{
          backgroundImage:
            "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(200,230,255,0.9) 3px, rgba(200,230,255,0.9) 4px)",
        }}
      />
      <motion.div
        className="absolute inset-x-0 -top-1/2 h-[200%] w-full opacity-20 mix-blend-soft-light will-change-transform"
        style={{
          background:
            "repeating-linear-gradient(0deg, transparent 0px, transparent 6px, rgba(125,211,252,0.035) 6px, rgba(125,211,252,0.035) 7px)",
        }}
        animate={{ y: ["0%", "50%"] }}
        transition={{ duration: 11, repeat: Infinity, ease: "linear" }}
      />
      <div className="absolute bottom-4 left-4 h-8 w-8 border-b border-l border-sky-300/[0.16]" />
      <div className="absolute bottom-4 right-4 h-8 w-8 border-b border-r border-sky-300/[0.16]" />
    </div>
  );
}
