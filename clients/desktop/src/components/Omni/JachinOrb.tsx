import React from "react";
import { motion } from "framer-motion";

export type AiState = "idle" | "listening" | "thinking" | "speaking";

type Palette = {
  main: string;
  glow: string;
  text: string;
  outerDuration: number;
  middleDuration: number;
  innerDuration: number;
  pulseDuration: number;
};

const PALETTE: Record<AiState, Palette> = {
  idle: {
    main: "#00E5FF",
    glow: "rgba(0,229,255,0.55)",
    text: "#CFF9FF",
    outerDuration: 24,
    middleDuration: 14,
    innerDuration: 9,
    pulseDuration: 2.8,
  },
  listening: {
    main: "#00E676",
    glow: "rgba(0,230,118,0.58)",
    text: "#D8FFE9",
    outerDuration: 18,
    middleDuration: 10,
    innerDuration: 7,
    pulseDuration: 1.9,
  },
  thinking: {
    main: "#B388FF",
    glow: "rgba(179,136,255,0.6)",
    text: "#EFE4FF",
    outerDuration: 13,
    middleDuration: 7,
    innerDuration: 5,
    pulseDuration: 1.3,
  },
  speaking: {
    main: "#FFCA28",
    glow: "rgba(255,202,40,0.62)",
    text: "#FFF4CB",
    outerDuration: 16,
    middleDuration: 9,
    innerDuration: 6,
    pulseDuration: 0.75,
  },
};

export interface JachinOrbProps {
  state: AiState;
  label?: string;
}

function TickMarks({ color }: { color: string }) {
  const ticks = Array.from({ length: 12 }, (_, i) => i * 30);
  return (
    <div className="pointer-events-none absolute inset-0 transform-gpu will-change-transform">
      {ticks.map((deg) => (
        <span
          key={deg}
          className="absolute left-1/2 top-1/2 h-[2px] w-[10px] -translate-y-1/2 rounded-full opacity-70 transform-gpu will-change-transform"
          style={{
            backgroundColor: color,
            transform: `translate(-50%, -50%) rotate(${deg}deg) translateX(56px)`,
          }}
        />
      ))}
    </div>
  );
}

export function JachinOrb({ state, label = "JACHIN" }: JachinOrbProps) {
  const p = PALETTE[state];
  const speaking = state === "speaking";
  const thinking = state === "thinking";
  return (
    <motion.div
      className="pointer-events-none relative flex h-[132px] w-[132px] transform-gpu items-center justify-center rounded-full will-change-transform"
      animate={{
        filter: [
          `drop-shadow(0 0 8px ${p.glow}) drop-shadow(0 0 18px ${p.glow})`,
          `drop-shadow(0 0 14px ${p.glow}) drop-shadow(0 0 30px ${p.glow})`,
          `drop-shadow(0 0 8px ${p.glow}) drop-shadow(0 0 18px ${p.glow})`,
        ],
      }}
      transition={{ duration: p.pulseDuration * 2, repeat: Infinity, ease: "easeInOut" }}
    >
      <motion.div
        className="absolute inset-[8px] transform-gpu rounded-full will-change-transform"
        style={{ boxShadow: `inset 0 0 24px ${p.glow}, 0 0 42px ${p.glow}` }}
        animate={thinking ? { scale: [1, 1.03, 1], opacity: [0.35, 0.58, 0.35] } : { opacity: 0.35 }}
        transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
      />

      <motion.div
        className="absolute inset-[5px] transform-gpu rounded-full border will-change-transform"
        style={{ borderColor: `${p.main}66` }}
        animate={{ rotate: 360, borderColor: [`${p.main}55`, `${p.main}AA`, `${p.main}55`] }}
        transition={{
          rotate: { duration: p.outerDuration, repeat: Infinity, ease: "linear" },
          borderColor: { duration: 2.2, repeat: Infinity, ease: "easeInOut" },
        }}
      />
      <TickMarks color={p.main} />

      <motion.div
        className="absolute inset-[16px] transform-gpu rounded-full border-2 will-change-transform"
        style={{ borderColor: `${p.main}AA`, borderStyle: "dashed" }}
        animate={{ rotate: -360 }}
        transition={{ duration: p.middleDuration, repeat: Infinity, ease: "linear" }}
      />

      <motion.div
        className="absolute inset-[26px] transform-gpu rounded-full border will-change-transform"
        style={{
          borderColor: `${p.main}CC`,
          borderTopColor: "transparent",
          borderBottomColor: "transparent",
        }}
        animate={{ rotate: 360 }}
        transition={{ duration: p.innerDuration, repeat: Infinity, ease: "linear" }}
      />

      <motion.div
        className="absolute flex h-[52px] w-[52px] transform-gpu items-center justify-center rounded-full border will-change-transform"
        style={{
          borderColor: `${p.main}CC`,
          background:
            "radial-gradient(circle at 35% 30%, rgba(255,255,255,0.24) 0%, rgba(255,255,255,0.08) 28%, rgba(0,0,0,0.22) 100%)",
          boxShadow: `inset 0 0 24px ${p.glow}, 0 0 28px ${p.glow}`,
        }}
        animate={
          speaking
            ? {
                scale: [0.92, 1.06, 0.96, 1.1, 0.9],
                opacity: [0.78, 1, 0.86, 1, 0.78],
              }
            : {
                scale: [0.96, 1.03, 0.96],
                opacity: [0.82, 1, 0.82],
              }
        }
        transition={{ duration: p.pulseDuration, repeat: Infinity, ease: "easeInOut" }}
      >
        <span
          className="select-none text-[11px] font-semibold tracking-[0.22em]"
          style={{ color: p.text, textShadow: `0 0 10px ${p.glow}` }}
        >
          {label}
        </span>
      </motion.div>
    </motion.div>
  );
}

export default JachinOrb;
