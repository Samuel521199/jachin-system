/// <reference types="vite/client" />

/** 浏览器流式语音识别（Web Speech API） */
interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}
interface SpeechRecognitionResultList {
  length: number;
  item(index: number): SpeechRecognitionResult;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionResult {
  length: number;
  item(index: number): SpeechRecognitionAlternative;
  [index: number]: SpeechRecognitionAlternative;
  isFinal: boolean;
}
interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}
interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onerror: ((e: Event) => void) | null;
  onend: ((e: Event) => void) | null;
}
interface SpeechRecognitionConstructor {
  new (): SpeechRecognition;
}
declare const SpeechRecognition: SpeechRecognitionConstructor | undefined;
declare const webkitSpeechRecognition: SpeechRecognitionConstructor | undefined;

interface ImportMetaEnv {
  readonly VITE_DAPR_HTTP_PORT?: string;
  readonly VITE_BACKEND_URL?: string;
  readonly VITE_USE_DAPR?: string;
  /** 为 true 时流式聊天直连后端（不走 Dapr），用于本地模型避免 Dapr 缓冲导致无回复 */
  readonly VITE_CHAT_STREAM_VIA_DIRECT?: string;
  readonly VITE_ENVIRONMENT?: string;
  readonly VITE_MODEL_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
