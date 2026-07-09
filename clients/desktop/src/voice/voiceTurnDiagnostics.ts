import type { VoiceUxProfile } from "./voiceProfiles";

export type VoiceTurnDiagnosticEvent = {
  stage: string;
  atMs: number;
  elapsedMs?: number;
  sincePrevMs?: number;
  payload: Record<string, unknown>;
};

export type VoiceTurnDiagnostics = {
  traceId: string;
  profile: VoiceUxProfile | string;
  startedAt: string;
  elapsedMs: number;
  eventCount: number;
  stt?: Record<string, unknown>;
  sv?: Record<string, unknown>;
  tts?: Record<string, unknown>;
  l3?: Record<string, unknown>;
  errors: Array<Record<string, unknown>>;
  events: VoiceTurnDiagnosticEvent[];
};

type ActiveDiagnostics = {
  traceId: string;
  profile: VoiceUxProfile | string;
  startedAtMs: number;
  startedAtIso: string;
  events: VoiceTurnDiagnosticEvent[];
  stt: Record<string, unknown>;
  sv: Record<string, unknown>;
  tts: Record<string, unknown>;
  l3: Record<string, unknown>;
  errors: Array<Record<string, unknown>>;
};

let active: ActiveDiagnostics | null = null;
let lastSnapshot: VoiceTurnDiagnostics | null = null;

function copyObj(obj: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(obj ?? {})) as Record<string, unknown>;
}

function compactPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload || {})) {
    if (key === "ui" || key === "understanding" || key === "replyPlan") continue;
    if (typeof value === "string") out[key] = value.length > 500 ? `${value.slice(0, 500)}...(${value.length})` : value;
    else out[key] = value;
  }
  return out;
}

function mergeSelected(target: Record<string, unknown>, payload: Record<string, unknown>, keys: string[]): void {
  for (const key of keys) {
    if (payload[key] !== undefined) target[key] = payload[key];
  }
}

function recordSummary(stage: string, payload: Record<string, unknown>): void {
  if (!active) return;
  if (stage.startsWith("stt.")) {
    active.stt.last_stage = stage;
    mergeSelected(active.stt, payload, [
      "profile",
      "text",
      "rawText",
      "correctedText",
      "userMessage",
      "confidence",
      "backend",
      "durationMs",
      "language",
      "hotwordCount",
      "hotwordStatus",
      "hotwordSources",
      "hotwordDominated",
      "hotwordDominationReasons",
      "latencyMs",
      "pipelineMs",
      "source",
      "finalized",
      "provisional",
      "streamText",
      "streamFinalChanged",
      "error",
      "code",
    ]);
  } else if (stage.startsWith("sv.")) {
    active.sv.last_stage = stage;
    mergeSelected(active.sv, payload, [
      "profile",
      "accepted",
      "usedOwnerTrack",
      "reason",
      "ownerDurationMs",
      "skippedSegmentsCount",
      "latencyMs",
      "error",
    ]);
  } else if (stage.startsWith("tts.")) {
    active.tts.last_stage = stage;
    mergeSelected(active.tts, payload, [
      "sentence",
      "text",
      "textLen",
      "spokenIndex",
      "kind",
      "reason",
      "sessionId",
      "voice",
      "status",
      "ok",
      "latencyMs",
      "serverSynthMs",
      "audioDurationMs",
      "attempts",
      "quality",
      "ttsKind",
      "styleIndex",
      "styleMode",
      "rawDurationMs",
      "trimLeadingMs",
      "trimTrailingMs",
      "bytes",
      "type",
      "totalMs",
      "err",
      "generation",
    ]);
  } else if (stage.startsWith("l3.")) {
    active.l3.last_stage = stage;
    mergeSelected(active.l3, payload, [
      "recognizedText",
      "wireText",
      "intentPreview",
      "answerPreview",
      "answerLen",
      "latencyMs",
      "sessionId",
      "turnToken",
      "sensoryConnected",
      "l2Available",
    ]);
  }

  if (stage.includes("fail") || stage.includes("error") || payload.error || payload.err || payload.code === "unknown") {
    active.errors.push({ stage, ...compactPayload(payload) });
  }
}

export function beginVoiceTurnDiagnostics(traceId: string, profile: VoiceUxProfile | string): void {
  active = {
    traceId,
    profile,
    startedAtMs: Date.now(),
    startedAtIso: new Date().toISOString(),
    events: [],
    stt: {},
    sv: {},
    tts: {},
    l3: {},
    errors: [],
  };
  lastSnapshot = null;
}

export function recordVoiceTurnDiagnosticEvent(
  stage: string,
  payload: Record<string, unknown>,
  elapsedMs?: number,
  sincePrevMs?: number,
): void {
  if (!active) return;
  const compact = compactPayload(payload);
  active.events.push({
    stage,
    atMs: Date.now(),
    elapsedMs,
    sincePrevMs,
    payload: compact,
  });
  if (active.events.length > 80) active.events.splice(0, active.events.length - 80);
  recordSummary(stage, compact);
}

export function snapshotVoiceTurnDiagnostics(): VoiceTurnDiagnostics | null {
  if (!active) return lastSnapshot;
  return {
    traceId: active.traceId,
    profile: active.profile,
    startedAt: active.startedAtIso,
    elapsedMs: Date.now() - active.startedAtMs,
    eventCount: active.events.length,
    stt: Object.keys(active.stt).length ? copyObj(active.stt) : undefined,
    sv: Object.keys(active.sv).length ? copyObj(active.sv) : undefined,
    tts: Object.keys(active.tts).length ? copyObj(active.tts) : undefined,
    l3: Object.keys(active.l3).length ? copyObj(active.l3) : undefined,
    errors: active.errors.slice(-12).map(copyObj),
    events: active.events.slice(-40).map((ev) => ({ ...ev, payload: copyObj(ev.payload) })),
  };
}

export function endVoiceTurnDiagnostics(outcome: string, extra: Record<string, unknown> = {}): VoiceTurnDiagnostics | null {
  recordVoiceTurnDiagnosticEvent("turn.end", { outcome, ...extra });
  lastSnapshot = snapshotVoiceTurnDiagnostics();
  active = null;
  return lastSnapshot;
}

