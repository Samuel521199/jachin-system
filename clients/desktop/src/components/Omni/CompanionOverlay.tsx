/**
 * 陪伴态 UI 壳层 — 与 chat.tsx 大窗逻辑隔离。
 *
 * 语音 / L3 / 路由逻辑留在 chat.tsx，经 props 回调注入；
 * 布局与 Orb 渲染在 OmniMiniSpark → OrbWindow。
 *
 * 改功能代码时优先改 chat.tsx 的 callback，勿改本文件结构。
 * 布局契约见 companionLayout.ts 与 COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md
 */
import React from "react";
import { motion } from "framer-motion";
import { OmniMiniSpark } from "./OmniMiniSpark";
import type { AiState } from "./JachinOrb";

export interface CompanionOverlayProps {
  state: AiState;
  isRecording: boolean;
  onExpandFull: () => void;
  onBargeIn: () => void;
  onVoiceStart: () => void;
  onVoiceStop: () => void;
  onQuickSend: (text: string) => void;
}

export function CompanionOverlay({
  state,
  isRecording,
  onExpandFull,
  onBargeIn,
  onVoiceStart,
  onVoiceStop,
  onQuickSend,
}: CompanionOverlayProps) {
  return (
    <motion.div
      key="omni-spark"
      data-companion-shell
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
      className="pointer-events-auto flex w-full shrink-0 flex-col items-center justify-start overflow-visible"
    >
      <OmniMiniSpark
        state={state}
        onExpandFull={onExpandFull}
        onBargeIn={onBargeIn}
        isRecording={isRecording}
        onVoiceStart={onVoiceStart}
        onVoiceStop={onVoiceStop}
        onQuickSend={onQuickSend}
      />
    </motion.div>
  );
}

export default CompanionOverlay;
