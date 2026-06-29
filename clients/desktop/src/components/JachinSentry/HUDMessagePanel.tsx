import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { emit, listen, type UnlistenFn } from "@tauri-apps/api/event";
import { useSensoryWebSocket, type SensoryAnswerMeta, type SensoryChunkMeta } from "../../hooks/useSensoryWebSocket";
import { mergeStreamChunk } from "../../utils/streamChunkMerge";
import { loadSessionsState } from "../../utils/chatSessionsStore";
import {
  armCompanionVoiceSession,
  emitCompanionTtsToChat,
  requestCompanionL3Send,
  VOICE_COMPANION_L3_EVENT,
  VOICE_COMPANION_USER_EVENT,
  type VoiceCompanionL3Payload,
} from "../../voice/voiceCompanionBridge";
import { initVoiceCompanionDebugLog, voiceCompanionDebug } from "../../voice/voiceCompanionDebugLog";

type HudRole = "assistant" | "user" | "system";
type HudMessage = { id: string; role: HudRole; content: string; ts: number };
type AppendSource = "utterance" | "tauri-user" | "tauri-assistant" | "ws-answer" | "system";

type AppendOpts = {
  runId?: string;
  source?: AppendSource;
  skipDedupe?: boolean;
};

const MAX_MESSAGES = 8;
const INACTIVE_HIDE_MS = 3 * 60 * 1000;
const DEDUPE_MS = 500;
const CHAT_SESSIONS_KEY = "jachin_chat_sessions_v2";
const HUD_SELF_L3_STREAM_ENABLED = false;

function clippedPush(list: HudMessage[], msg: HudMessage) {
  const merged = [...list, msg];
  return merged.length > MAX_MESSAGES ? merged.slice(merged.length - MAX_MESSAGES) : merged;
}

function resolveChatSessionId(): string {
  return loadSessionsState().currentId;
}

function newMessageId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function dedupeKey(role: HudRole, content: string, runId?: string): string {
  if (runId) return `${role}:${runId}:${content}`;
  return `${role}:${content}`;
}

function renderHudText(content: string) {
  const parts = content.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, idx) => {
    const m = /^\*\*(.+)\*\*$/.exec(part);
    if (!m) return <React.Fragment key={idx}>{part}</React.Fragment>;
    return (
      <span
        key={idx}
        className="text-cyan-300 font-bold drop-shadow-[0_0_6px_rgba(103,232,249,0.95)]"
      >
        {m[1]}
      </span>
    );
  });
}

export function HUDMessagePanel() {
  const [messages, setMessages] = useState<HudMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [connectedOnce, setConnectedOnce] = useState(false);
  const [sending, setSending] = useState(false);

  const streamAccRef = useRef("");
  const streamingAssistantIdRef = useRef<string | null>(null);
  const activeRunIdRef = useRef("");
  const voiceSessionActiveRef = useRef(false);
  const recentAppendsRef = useRef<Map<string, number>>(new Map());
  const inactiveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const orbIdleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const manuallyDismissedRef = useRef(false);
  const sessionRef = useRef(resolveChatSessionId());
  /** chat 已通过 voice-companion-l3 桥接流式 UI 时，HUD 自有 WS 不再重复写气泡 */
  const streamFromChatBridgeRef = useRef(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  const sensory = useSensoryWebSocket({ desktopSessionIdRef: sessionRef });
  const { connected, registerChunkHandler, registerAnswerHandler, registerStepHandler } = sensory;

  const refreshSessionId = useCallback(() => {
    sessionRef.current = resolveChatSessionId();
  }, []);

  const setOrbState = useCallback((state: "idle" | "thinking" | "speaking" | "listening") => {
    if (orbIdleTimerRef.current) {
      clearTimeout(orbIdleTimerRef.current);
      orbIdleTimerRef.current = null;
    }
    void emit("hud-orb-state", { state }).catch(() => {});
  }, []);

  const setOrbIdleDelayed = useCallback(() => {
    if (orbIdleTimerRef.current) clearTimeout(orbIdleTimerRef.current);
    orbIdleTimerRef.current = setTimeout(() => {
      void emit("hud-orb-state", { state: "idle" }).catch(() => {});
      orbIdleTimerRef.current = null;
    }, 2000);
  }, []);

  const resetStreamingState = useCallback(() => {
    streamingAssistantIdRef.current = null;
    streamAccRef.current = "";
    activeRunIdRef.current = "";
    streamFromChatBridgeRef.current = false;
  }, []);

  const setVoiceSession = useCallback((active: boolean) => {
    const wasActive = voiceSessionActiveRef.current;
    voiceSessionActiveRef.current = active;
    voiceCompanionDebug("hud.voice_session", { active, wasActive });
    if (!active) {
      resetStreamingState();
    }
  }, [resetStreamingState]);

  const hidePanel = useCallback(async () => {
    manuallyDismissedRef.current = true;
    setVoiceSession(false);
    if (inactiveTimerRef.current) {
      clearTimeout(inactiveTimerRef.current);
      inactiveTimerRef.current = null;
    }
    try {
      await invoke("close_hud_panel");
    } catch (e) {
      console.warn("[HUD] close_hud_panel failed:", e);
      try {
        await invoke("set_hud_panel_suppressed", { suppressed: true });
        await getCurrentWindow().hide();
      } catch (e2) {
        console.warn("[HUD] fallback hide failed:", e2);
      }
    }
  }, [setVoiceSession]);

  const touchActivity = useCallback(() => {
    if (inactiveTimerRef.current) clearTimeout(inactiveTimerRef.current);
    inactiveTimerRef.current = setTimeout(() => {
      void hidePanel();
    }, INACTIVE_HIDE_MS);
  }, [hidePanel]);

  const revealPanel = useCallback((force = false) => {
    if (!force && manuallyDismissedRef.current) return;
    touchActivity();
    manuallyDismissedRef.current = false;
    if (force) {
      void invoke("set_hud_panel_suppressed", { suppressed: false }).catch((e) => {
        console.warn("[HUD] revealPanel clear suppression failed:", e);
      });
    }
    void getCurrentWindow().show().catch((e) => {
      console.warn("[HUD] revealPanel show failed:", e);
    });
  }, [touchActivity]);

  const appendMessage = useCallback((role: HudRole, content: string, opts?: AppendOpts) => {
    const t = content.trim();
    if (!t) return;

    const key = dedupeKey(role, t, opts?.runId);
    if (!opts?.skipDedupe) {
      const now = Date.now();
      const last = recentAppendsRef.current.get(key);
      if (last != null && now - last < DEDUPE_MS) {
        console.debug("[HUD] append dedupe skip", opts?.source ?? "unknown", role, t.slice(0, 40));
        return;
      }
      recentAppendsRef.current.set(key, now);
    }

    const msg: HudMessage = {
      id: newMessageId(),
      role,
      content: t,
      ts: Date.now(),
    };
    voiceCompanionDebug("hud.append", {
      source: opts?.source ?? "unknown",
      role,
      len: t.length,
      runId: opts?.runId ?? "",
    });
    setMessages((prev) => clippedPush(prev, msg));
  }, []);

  /** Single Writer 入口：本轮 user 只写一次；可选是否发往 L3 */
  const beginUtterance = useCallback(
    (text: string, opts?: { sendToL3?: boolean; source?: AppendSource }) => {
      const t = text.trim();
      if (!t) return false;

      refreshSessionId();
      setVoiceSession(true);
      revealPanel(true);
      appendMessage("user", t, { source: opts?.source ?? "utterance" });
      resetStreamingState();

      if (opts?.sendToL3 === false) {
        setOrbState("listening");
        return true;
      }

      setSending(true);
      void armCompanionVoiceSession();
      void requestCompanionL3Send(t);
      setOrbState("thinking");
      return true;
    },
    [appendMessage, refreshSessionId, resetStreamingState, revealPanel, setOrbState, setVoiceSession],
  );

  const handleL3StreamChunkUi = useCallback(
    (delta: string, runId?: string, meta?: SensoryChunkMeta) => {
      if (meta?.isReasoning) {
        setOrbState("thinking");
        return;
      }
      const rid = runId ?? "";
      if (rid) activeRunIdRef.current = rid;

      revealPanel(true);
      setOrbState("speaking");

      let bubbleId = streamingAssistantIdRef.current;
      setMessages((prev) => {
        if (bubbleId && prev.some((m) => m.id === bubbleId)) {
          return prev.map((m) =>
            m.id === bubbleId ? { ...m, content: `${m.content}${delta}` } : m,
          );
        }
        bubbleId = newMessageId();
        streamingAssistantIdRef.current = bubbleId;
        voiceCompanionDebug("hud.chunk_new_bubble", {
          runId: rid,
          deltaLen: delta.length,
          bubbleId,
        });
        return clippedPush(prev, {
          id: bubbleId,
          role: "assistant",
          content: delta,
          ts: Date.now(),
        });
      });
      voiceCompanionDebug("hud.chunk_ui", {
        runId: rid,
        delta: delta.slice(0, 80),
        bubbleId: streamingAssistantIdRef.current ?? "",
      });
    },
    [revealPanel, setOrbState],
  );

  const handleL3AnswerUi = useCallback(
    (answerContent: string, meta?: SensoryAnswerMeta) => {
      const rid = meta?.runId ?? "";
      const activeRid = activeRunIdRef.current;
      if (rid && activeRid && rid !== activeRid) {
        voiceCompanionDebug("hud.answer_skip_run", { rid, activeRid });
        return;
      }

      revealPanel(true);
      const outcome = meta?.terminalOutcome;
      const finalText = (answerContent || "").trim();
      if (outcome === "error" || outcome === "rejected") {
        appendMessage("system", answerContent || "本轮请求未完成。", {
          source: "system",
          skipDedupe: true,
        });
      } else if (!meta?.hadStreamChunks) {
        appendMessage("assistant", finalText || "已完成。", {
          source: "ws-answer",
          runId: rid || undefined,
        });
      } else if (finalText) {
        const bubbleId = streamingAssistantIdRef.current;
        setMessages((prev) => {
          if (bubbleId && prev.some((m) => m.id === bubbleId)) {
            return prev.map((m) =>
              m.id === bubbleId ? { ...m, content: finalText } : m,
            );
          }
          return clippedPush(prev, {
            id: newMessageId(),
            role: "assistant",
            content: finalText,
            ts: Date.now(),
          });
        });
        voiceCompanionDebug("hud.answer_finalize_stream", { runId: rid, len: finalText.length });
      }

      streamingAssistantIdRef.current = null;
      streamAccRef.current = "";
      setSending(false);
      setOrbIdleDelayed();
    },
    [appendMessage, revealPanel, setOrbIdleDelayed],
  );

  const handlersRef = useRef({
    beginUtterance,
    appendMessage,
    revealPanel,
    setOrbState,
    setOrbIdleDelayed,
    setVoiceSession,
    resetStreamingState,
    handleL3StreamChunkUi,
    handleL3AnswerUi,
  });

  useEffect(() => {
    handlersRef.current = {
      beginUtterance,
      appendMessage,
      revealPanel,
      setOrbState,
      setOrbIdleDelayed,
      setVoiceSession,
      resetStreamingState,
      handleL3StreamChunkUi,
      handleL3AnswerUi,
    };
  }, [
    appendMessage,
    beginUtterance,
    handleL3AnswerUi,
    handleL3StreamChunkUi,
    revealPanel,
    resetStreamingState,
    setOrbIdleDelayed,
    setOrbState,
    setVoiceSession,
  ]);

  useEffect(() => {
    void initVoiceCompanionDebugLog();
  }, []);

  useEffect(() => {
    if (!listRef.current) return;
    listRef.current.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (connected) setConnectedOnce(true);
  }, [connected]);

  useEffect(() => {
    touchActivity();
    return () => {
      if (inactiveTimerRef.current) clearTimeout(inactiveTimerRef.current);
      if (orbIdleTimerRef.current) clearTimeout(orbIdleTimerRef.current);
    };
  }, [touchActivity]);

  useEffect(() => {
    refreshSessionId();
    const onStorage = (ev: StorageEvent) => {
      if (ev.key === CHAT_SESSIONS_KEY) refreshSessionId();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [refreshSessionId]);

  useEffect(() => {
    const unsubs: UnlistenFn[] = [];
    let disposed = false;

    void (async () => {
      try {
        const uVoice = await listen<{ active?: boolean }>("hud-voice-session", (ev) => {
          if (disposed) return;
          const active = Boolean(ev.payload?.active);
          handlersRef.current.setVoiceSession(active);
        });
        if (disposed) {
          uVoice();
          return;
        }
        unsubs.push(uVoice);

        const uAssistant = await listen<{ title?: string; body?: string }>("hud-panel-message", (ev) => {
          if (disposed) return;
          const body = typeof ev.payload?.body === "string" ? ev.payload.body : "";
          const title = typeof ev.payload?.title === "string" ? ev.payload.title : "Jachin";
          if (!body.trim()) return;
          handlersRef.current.setVoiceSession(true);
          handlersRef.current.revealPanel();
          handlersRef.current.appendMessage("assistant", `${title}\n${body}`, { source: "tauri-assistant" });
        });
        if (disposed) {
          uAssistant();
          return;
        }
        unsubs.push(uAssistant);

        const uUser = await listen<{ content?: string }>("hud-panel-user-message", (ev) => {
          if (disposed) return;
          const content = typeof ev.payload?.content === "string" ? ev.payload.content : "";
          if (!content.trim()) return;
          handlersRef.current.beginUtterance(content, { sendToL3: false, source: "tauri-user" });
        });
        if (disposed) {
          uUser();
          return;
        }
        unsubs.push(uUser);

        const uCompanionUser = await listen<{ content?: string }>(VOICE_COMPANION_USER_EVENT, (ev) => {
          if (disposed) return;
          const content = typeof ev.payload?.content === "string" ? ev.payload.content : "";
          if (!content.trim()) return;
          handlersRef.current.setVoiceSession(true);
          handlersRef.current.revealPanel(true);
          handlersRef.current.appendMessage("user", content.trim(), { source: "tauri-user" });
          handlersRef.current.resetStreamingState();
        });
        if (disposed) {
          uCompanionUser();
          return;
        }
        unsubs.push(uCompanionUser);

        const uBridge = await listen<VoiceCompanionL3Payload>(VOICE_COMPANION_L3_EVENT, (ev) => {
          if (disposed) return;
          const p = ev.payload;
          voiceCompanionDebug("hud.bridge_event", { kind: p?.kind, runId: p?.runId });
          if (!p?.kind) return;
          if (p.kind === "thinking") {
            if (!voiceSessionActiveRef.current) handlersRef.current.setVoiceSession(true);
            streamFromChatBridgeRef.current = true;
            handlersRef.current.revealPanel(true);
            handlersRef.current.setOrbState("thinking");
            return;
          }
          if (p.kind === "chunk") {
            if (!voiceSessionActiveRef.current) handlersRef.current.setVoiceSession(true);
            streamFromChatBridgeRef.current = true;
            const { next, delta } = mergeStreamChunk(streamAccRef.current, p.delta ?? "");
            streamAccRef.current = next;
            if (!delta) return;
            handlersRef.current.handleL3StreamChunkUi(delta, p.runId, p.chunkMeta);
            return;
          }
          if (p.kind === "answer") {
            streamFromChatBridgeRef.current = true;
            handlersRef.current.handleL3AnswerUi(p.content ?? "", p.meta);
          }
        });
        if (disposed) {
          uBridge();
          return;
        }
        unsubs.push(uBridge);
      } catch (e) {
        console.warn("[HUD] tauri listen setup failed:", e);
      }
    })();

    return () => {
      disposed = true;
      for (const u of unsubs) u();
    };
  }, []);

  useEffect(() => {
    registerStepHandler((stepType, _content, runId) => {
      if (!HUD_SELF_L3_STREAM_ENABLED) return;
      if (!voiceSessionActiveRef.current) return;
      if (runId) activeRunIdRef.current = runId;
      if (stepType === "thought") handlersRef.current.setOrbState("thinking");
    });
    return () => registerStepHandler(null);
  }, [registerStepHandler]);

  useEffect(() => {
    registerChunkHandler((chunk: string, runId?: string, meta?: SensoryChunkMeta) => {
      if (!HUD_SELF_L3_STREAM_ENABLED) return;
      if (!voiceSessionActiveRef.current) return;
      if (streamFromChatBridgeRef.current) return;
      const { next, delta } = mergeStreamChunk(streamAccRef.current, chunk || "");
      streamAccRef.current = next;
      if (!delta) return;
      handleL3StreamChunkUi(delta, runId, meta);
      void emitCompanionTtsToChat({ kind: "chunk", delta, runId, chunkMeta: meta });
    });
    return () => registerChunkHandler(null);
  }, [handleL3StreamChunkUi, registerChunkHandler]);

  useEffect(() => {
    registerAnswerHandler((answerContent: string, meta?: SensoryAnswerMeta) => {
      if (!HUD_SELF_L3_STREAM_ENABLED) return;
      if (!voiceSessionActiveRef.current) return;
      if (streamFromChatBridgeRef.current) return;
      handleL3AnswerUi(answerContent, meta);
      void emitCompanionTtsToChat({ kind: "answer", content: answerContent, runId: meta?.runId, meta });
    });
    return () => registerAnswerHandler(null);
  }, [handleL3AnswerUi, registerAnswerHandler]);

  const sendQuick = useCallback(() => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    beginUtterance(text, { sendToL3: true, source: "utterance" });
  }, [beginUtterance, draft]);

  const onDragPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest("[data-hud-no-drag='1']")) return;
    touchActivity();
    void getCurrentWindow().startDragging().catch(() => {});
  }, [touchActivity]);

  useEffect(() => {
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        void hidePanel();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [hidePanel]);

  const stateHint = useMemo(() => {
    if (sending) return "RUNNING";
    if (connected) return "CONNECTED";
    if (connectedOnce) return "RECONNECTING";
    return "BOOTING";
  }, [sending, connected, connectedOnce]);

  return (
    <div className="h-full w-full">
      <motion.div
        initial={{ opacity: 0.86 }}
        animate={{ opacity: 1 }}
        className="relative flex h-full w-full flex-col overflow-hidden border border-white/10 bg-black/20 font-mono backdrop-blur-2xl shadow-[inset_0_0_24px_rgba(255,255,255,0.05),inset_0_0_30px_rgba(34,211,238,0.04),0_0_36px_rgba(34,211,238,0.2)]"
      >
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-cyan-100/8 via-transparent to-black/8" />
        <div
          className="pointer-events-none absolute left-4 top-0 h-[4px] w-[118px] bg-cyan-300"
          style={{
            clipPath: "polygon(10% 0, 100% 0, 90% 100%, 0% 100%)",
            boxShadow: "0 0 14px rgba(34,211,238,0.85), 0 0 28px rgba(34,211,238,0.45)",
          }}
        />

        <div
          className="relative z-10 h-7 shrink-0 cursor-move border-b border-white/10 bg-transparent px-3 py-1.5"
          onPointerDown={onDragPointerDown}
          title="拖动此处移动 HUD"
        >
          <div className="flex items-center justify-between text-[10px] tracking-[0.22em] text-cyan-50/80 [text-shadow:0_0_3px_rgba(207,250,254,0.9)]">
            <span>SYSTEM.HUD.V2</span>
            <div className="flex items-center gap-2" data-hud-no-drag="1">
              <span className="text-cyan-300/95 [text-shadow:0_0_6px_rgba(34,211,238,0.95)]">
                {stateHint}
              </span>
              <button
                type="button"
                onClick={hidePanel}
                onPointerDown={(e) => e.stopPropagation()}
                className="rounded-sm border border-cyan-200/35 px-1.5 py-0.5 text-[10px] leading-none text-cyan-100/90 hover:bg-cyan-300/15"
                title="关闭 HUD（Esc）"
              >
                x
              </button>
            </div>
          </div>
        </div>

        <div className="relative z-10 px-3 pt-2 pb-1 text-[10px] tracking-[0.2em] text-cyan-50/55">
          TRANSIENT.PANEL
        </div>

        <div
          ref={listRef}
          className="relative z-10 flex-1 space-y-1.5 overflow-y-auto px-3 py-2 text-[11px] leading-snug text-cyan-50/95 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          <AnimatePresence initial={false}>
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <motion.div
                  initial={{ opacity: 0, x: m.role === "user" ? 10 : -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: m.role === "user" ? 10 : -10 }}
                  className={`max-w-[96%] whitespace-pre-wrap px-0 py-0 text-[11px] ${
                    m.role === "user"
                      ? "text-right text-cyan-100/85 drop-shadow-[0_0_2px_rgba(207,250,254,0.45)]"
                      : m.role === "assistant"
                        ? "text-left text-cyan-50 drop-shadow-[0_0_2px_rgba(255,255,255,0.6)]"
                        : "text-left text-violet-100/90 drop-shadow-[0_0_2px_rgba(196,181,253,0.55)]"
                  }`}
                >
                  {renderHudText(m.content)}
                </motion.div>
              </div>
            ))}
          </AnimatePresence>
        </div>

        <div className="relative z-10 shrink-0 border-t border-white/10 px-2 py-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-cyan-300/85 [text-shadow:0_0_5px_rgba(34,211,238,0.8)]">{">"}</span>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onFocus={touchActivity}
              onKeyDown={(e) => {
                if (e.key === "Enter") sendQuick();
              }}
              placeholder="Awaiting command..."
              className="w-full bg-transparent px-0 py-1 text-[10px] text-cyan-50 placeholder:text-cyan-100/40 outline-none"
            />
            <button
              type="button"
              onClick={() => {
                void invoke("show_chat_window").catch(() => {});
              }}
              className="px-2 py-1 text-[10px] text-cyan-100/70 hover:text-cyan-50 hover:drop-shadow-[0_0_6px_rgba(103,232,249,0.9)]"
            >
              +
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  );
}

export default HUDMessagePanel;
