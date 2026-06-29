import React from "react";
import OrbWindow from "./OrbWindow";
import type { AiState } from "./JachinOrb";

export interface OmniMiniSparkProps {
  state: AiState;
  onExpandFull: () => void;
  onQuickSend?: (text: string) => void;
  onBargeIn?: () => void;
  isRecording?: boolean;
  onVoiceStart?: () => void;
  onVoiceStop?: () => void;
}

export const OmniMiniSpark: React.FC<OmniMiniSparkProps> = ({
  state,
  onExpandFull,
  onQuickSend,
  onBargeIn,
  isRecording,
  onVoiceStart,
  onVoiceStop,
}) => {
  return (
    <OrbWindow
      state={state}
      onExpandFull={onExpandFull}
      onQuickSend={onQuickSend}
      onBargeIn={onBargeIn}
      isRecording={isRecording}
      onVoiceStart={onVoiceStart}
      onVoiceStop={onVoiceStop}
    />
  );
};

export default OmniMiniSpark;
