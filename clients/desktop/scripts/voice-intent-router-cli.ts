/**
 * 陪伴态语音路由 CLI — SSOT 为 ../src/voice/voiceIntentRouter.ts
 *
 * stdin JSON: { "text": string, "ctx"?: VoiceDispatcherContext }
 * stdout JSON: VoiceDispatcherDecision
 *
 * 供 Python benchmark / 脚本 subprocess 调用，避免重复维护路由规则。
 */
import {
  dispatchVoiceIntent,
  type VoiceDispatcherContext,
} from "../src/voice/voiceIntentRouter";

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  return Buffer.concat(chunks).toString("utf-8").trim();
}

async function main(): Promise<void> {
  const raw = await readStdin();
  if (!raw) {
    console.error("voice-intent-router-cli: empty stdin");
    process.exit(2);
  }
  let payload: { text?: string; ctx?: VoiceDispatcherContext };
  try {
    payload = JSON.parse(raw) as { text?: string; ctx?: VoiceDispatcherContext };
  } catch (e) {
    console.error(`voice-intent-router-cli: invalid JSON: ${e}`);
    process.exit(2);
  }
  const text = typeof payload.text === "string" ? payload.text : "";
  const ctx: VoiceDispatcherContext = payload.ctx ?? { activeTasks: [] };
  const decision = dispatchVoiceIntent(text, ctx);
  process.stdout.write(`${JSON.stringify(decision)}\n`);
}

main().catch((e) => {
  console.error(`voice-intent-router-cli: ${e}`);
  process.exit(1);
});
