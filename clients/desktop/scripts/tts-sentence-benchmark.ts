import fs from "node:fs/promises";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { spawn } from "node:child_process";
import { splitSentences } from "../src/voice/sentenceBuffer";
import { prepareSentenceForTts } from "../src/voice/speakableText";

type TtsEngine = "jvs" | "l2";

type Args = {
  text: string;
  voice: string;
  engine: TtsEngine;
  chunkSize: number;
  chunkDelayMs: number;
  outDir: string;
  play: boolean;
  noFilter: boolean;
};

type SentenceMetric = {
  index: number;
  rawSentence: string;
  speakable: string | null;
  skipped: boolean;
  splitReadyAtMs: number;
  ttsStartAtMs: number | null;
  ttsEndAtMs: number | null;
  ttsLatencyMs: number | null;
  gapFromPrevSplitReadyMs: number | null;
  gapFromPrevTtsEndMs: number | null;
  audioBytes: number;
  audioDurationMs: number | null;
  audioFilePath: string | null;
  error: string | null;
};

type Report = {
  startedAtIso: string;
  engine: TtsEngine;
  voice: string;
  chunkSize: number;
  chunkDelayMs: number;
  inputLength: number;
  sentenceCountRaw: number;
  sentenceCountSpeakable: number;
  sentenceCountSkipped: number;
  totalElapsedMs: number;
  ttsLatencyAvgMs: number | null;
  ttsLatencyP50Ms: number | null;
  ttsLatencyP95Ms: number | null;
  gapFromPrevSplitReadyAvgMs: number | null;
  gapFromPrevTtsEndAvgMs: number | null;
  metrics: SentenceMetric[];
};

function parseArgs(argv: string[]): Partial<Args> {
  const parsed: Partial<Args> = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const val = argv[i + 1];
    if (val == null || val.startsWith("--")) continue;
    i += 1;
    if (key === "text") parsed.text = val;
    else if (key === "voice") parsed.voice = val;
    else if (key === "engine" && (val === "jvs" || val === "l2")) parsed.engine = val;
    else if (key === "chunk-size") parsed.chunkSize = Math.max(1, Number(val) || 24);
    else if (key === "chunk-delay-ms") parsed.chunkDelayMs = Math.max(0, Number(val) || 0);
    else if (key === "out-dir") parsed.outDir = val;
    else if (key === "text-file") parsed.text = `@file:${val}`;
    else if (key === "play") parsed.play = val === "1" || val.toLowerCase() === "true";
    else if (key === "no-filter") parsed.noFilter = val === "1" || val.toLowerCase() === "true";
  }
  return parsed;
}

async function resolveInputText(rawTextArg: string | undefined): Promise<string> {
  if (!rawTextArg) {
    return "你好。请帮我总结今天的工作重点，然后给我两个可执行建议。最后再提醒我晚点喝水。";
  }
  if (rawTextArg.startsWith("@file:")) {
    const filePath = rawTextArg.slice("@file:".length);
    const content = await fs.readFile(filePath, "utf8");
    return content.trim();
  }
  return rawTextArg;
}

async function sleep(ms: number): Promise<void> {
  if (ms <= 0) return;
  await new Promise<void>((resolve) => setTimeout(resolve, ms));
}

function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[idx];
}

function average(values: Array<number | null>): number | null {
  const nums = values.filter((v): v is number => typeof v === "number");
  if (nums.length === 0) return null;
  return nums.reduce((s, n) => s + n, 0) / nums.length;
}

function chunkText(text: string, size: number): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += size) {
    chunks.push(text.slice(i, i + size));
  }
  return chunks;
}

function parseWavDurationMs(buffer: ArrayBuffer): number | null {
  if (buffer.byteLength < 44) return null;
  const view = new DataView(buffer);
  const riff =
    String.fromCharCode(view.getUint8(0)) +
    String.fromCharCode(view.getUint8(1)) +
    String.fromCharCode(view.getUint8(2)) +
    String.fromCharCode(view.getUint8(3));
  const wave =
    String.fromCharCode(view.getUint8(8)) +
    String.fromCharCode(view.getUint8(9)) +
    String.fromCharCode(view.getUint8(10)) +
    String.fromCharCode(view.getUint8(11));
  if (riff !== "RIFF" || wave !== "WAVE") return null;

  let offset = 12;
  let byteRate: number | null = null;
  let dataSize: number | null = null;
  while (offset + 8 <= view.byteLength) {
    const id =
      String.fromCharCode(view.getUint8(offset)) +
      String.fromCharCode(view.getUint8(offset + 1)) +
      String.fromCharCode(view.getUint8(offset + 2)) +
      String.fromCharCode(view.getUint8(offset + 3));
    const size = view.getUint32(offset + 4, true);
    const dataOffset = offset + 8;
    if (id === "fmt " && size >= 16) {
      byteRate = view.getUint32(dataOffset + 8, true);
    } else if (id === "data") {
      dataSize = size;
      break;
    }
    offset = dataOffset + size + (size % 2);
  }
  if (!byteRate || !dataSize || byteRate <= 0) return null;
  return (dataSize / byteRate) * 1000;
}

async function synthesizeByEngine(engine: TtsEngine, text: string, voice: string): Promise<Blob> {
  if (engine === "jvs") {
    const base = process.env.JVS_BASE_URL || "http://127.0.0.1:18982";
    const res = await fetch(`${base}/v1/tts/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice }),
    });
    if (!res.ok) {
      throw new Error(`JVS ${res.status}: ${await res.text()}`);
    }
    return res.blob();
  }
  const base = process.env.L2_BASE_URL || "http://127.0.0.1:18888";
  const res = await fetch(`${base}/api/v2/voice/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      voice,
      language: "zh-CN",
      speed: 1.0,
      pitch: 1.0,
    }),
  });
  if (!res.ok) {
    throw new Error(`L2 ${res.status}: ${await res.text()}`);
  }
  return res.blob();
}

async function playWavOnWindows(filePath: string): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const escapedPath = filePath.replace(/'/g, "''");
    const cmd = [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      `(New-Object Media.SoundPlayer '${escapedPath}').PlaySync()`,
    ];
    const ps = spawn("powershell", cmd, { stdio: "ignore" });
    ps.on("error", reject);
    ps.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`powershell playback failed, exit=${String(code)}`));
    });
  });
}

function toMd(report: Report): string {
  const lines: string[] = [];
  lines.push("# TTS Sentence Benchmark Report");
  lines.push("");
  lines.push(`- Started At: ${report.startedAtIso}`);
  lines.push(`- Engine: ${report.engine}`);
  lines.push(`- Voice: ${report.voice}`);
  lines.push(`- Input Length: ${report.inputLength}`);
  lines.push(`- Chunk Size: ${report.chunkSize}`);
  lines.push(`- Chunk Delay(ms): ${report.chunkDelayMs}`);
  lines.push(`- Total Elapsed(ms): ${report.totalElapsedMs.toFixed(2)}`);
  lines.push(`- Raw Sentences: ${report.sentenceCountRaw}`);
  lines.push(`- Speakable Sentences: ${report.sentenceCountSpeakable}`);
  lines.push(`- Skipped Sentences: ${report.sentenceCountSkipped}`);
  lines.push(`- TTS Latency Avg(ms): ${report.ttsLatencyAvgMs?.toFixed(2) ?? "n/a"}`);
  lines.push(`- TTS Latency P50(ms): ${report.ttsLatencyP50Ms?.toFixed(2) ?? "n/a"}`);
  lines.push(`- TTS Latency P95(ms): ${report.ttsLatencyP95Ms?.toFixed(2) ?? "n/a"}`);
  lines.push(`- Gap Split Ready Avg(ms): ${report.gapFromPrevSplitReadyAvgMs?.toFixed(2) ?? "n/a"}`);
  lines.push(`- Gap Prev TTS End Avg(ms): ${report.gapFromPrevTtsEndAvgMs?.toFixed(2) ?? "n/a"}`);
  lines.push("");
  lines.push("## Per Sentence");
  lines.push("");
  lines.push("| # | rawSentence | speakable | splitReadyMs | ttsLatencyMs | gapPrevSplitMs | gapPrevTtsEndMs | audioDurationMs | error |");
  lines.push("|---|---|---|---:|---:|---:|---:|---:|---|");
  for (const m of report.metrics) {
    lines.push(
      `| ${m.index} | ${m.rawSentence.replace(/\|/g, "\\|")} | ${(m.speakable ?? "null").replace(/\|/g, "\\|")} | ${m.splitReadyAtMs.toFixed(2)} | ${m.ttsLatencyMs?.toFixed(2) ?? "n/a"} | ${m.gapFromPrevSplitReadyMs?.toFixed(2) ?? "n/a"} | ${m.gapFromPrevTtsEndMs?.toFixed(2) ?? "n/a"} | ${m.audioDurationMs?.toFixed(2) ?? "n/a"} | ${m.error ?? ""} |`,
    );
  }
  lines.push("");
  return lines.join("\n");
}

async function main(): Promise<void> {
  const partial = parseArgs(process.argv.slice(2));
  const text = await resolveInputText(partial.text);
  const args: Args = {
    text,
    voice: partial.voice || "zm_053",
    engine: partial.engine || "jvs",
    chunkSize: partial.chunkSize || 24,
    chunkDelayMs: partial.chunkDelayMs || 0,
    outDir: partial.outDir || path.resolve("tmp", "tts-benchmark"),
    play: Boolean(partial.play),
    noFilter: Boolean(partial.noFilter),
  };

  const startedAtIso = new Date().toISOString();
  const t0 = performance.now();
  const audioOutDir = path.join(args.outDir, "audio");
  if (args.play) {
    await fs.mkdir(audioOutDir, { recursive: true });
  }
  const chunks = chunkText(args.text, args.chunkSize);

  let buffer = "";
  const collected: Array<{ sentence: string; readyAtMs: number }> = [];
  for (const chunk of chunks) {
    const split = splitSentences(buffer, chunk);
    buffer = split.remainder;
    const readyAt = performance.now() - t0;
    for (const sentence of split.complete) {
      collected.push({ sentence, readyAtMs: readyAt });
    }
    if (args.chunkDelayMs > 0) {
      await sleep(args.chunkDelayMs);
    }
  }
  if (buffer.trim()) {
    collected.push({ sentence: buffer.trim(), readyAtMs: performance.now() - t0 });
  }

  const metrics: SentenceMetric[] = [];
  let prevReadyAt: number | null = null;
  let prevTtsEndAt: number | null = null;

  for (let i = 0; i < collected.length; i += 1) {
    const it = collected[i];
    const speakable = args.noFilter ? it.sentence.trim() || null : prepareSentenceForTts(it.sentence);
    const row: SentenceMetric = {
      index: i + 1,
      rawSentence: it.sentence,
      speakable,
      skipped: !speakable,
      splitReadyAtMs: it.readyAtMs,
      ttsStartAtMs: null,
      ttsEndAtMs: null,
      ttsLatencyMs: null,
      gapFromPrevSplitReadyMs: prevReadyAt == null ? null : it.readyAtMs - prevReadyAt,
      gapFromPrevTtsEndMs: null,
      audioBytes: 0,
      audioDurationMs: null,
      audioFilePath: null,
      error: null,
    };

    if (speakable) {
      const tStart = performance.now();
      row.ttsStartAtMs = tStart - t0;
      if (prevTtsEndAt != null) {
        row.gapFromPrevTtsEndMs = row.ttsStartAtMs - prevTtsEndAt;
      }
      try {
        const blob = await synthesizeByEngine(args.engine, speakable, args.voice);
        const tEnd = performance.now();
        row.ttsEndAtMs = tEnd - t0;
        row.ttsLatencyMs = row.ttsEndAtMs - row.ttsStartAtMs;
        row.audioBytes = blob.size;
        const arr = await blob.arrayBuffer();
        row.audioDurationMs = parseWavDurationMs(arr);
        if (args.play) {
          const filePath = path.join(audioOutDir, `sentence-${String(row.index).padStart(2, "0")}.wav`);
          row.audioFilePath = filePath;
          await fs.writeFile(filePath, Buffer.from(arr));
          console.log(
            `[tts-benchmark] play sentence #${row.index}: ${row.speakable ?? row.rawSentence}`,
          );
          await playWavOnWindows(filePath);
        }
        prevTtsEndAt = row.ttsEndAtMs;
      } catch (e) {
        const tEnd = performance.now();
        row.ttsEndAtMs = tEnd - t0;
        row.ttsLatencyMs = row.ttsEndAtMs - row.ttsStartAtMs;
        row.error = String(e);
      }
    }

    metrics.push(row);
    prevReadyAt = it.readyAtMs;
  }

  const elapsed = performance.now() - t0;
  const ttsLatencies = metrics
    .map((m) => m.ttsLatencyMs)
    .filter((v): v is number => typeof v === "number");

  const report: Report = {
    startedAtIso,
    engine: args.engine,
    voice: args.voice,
    chunkSize: args.chunkSize,
    chunkDelayMs: args.chunkDelayMs,
    inputLength: args.text.length,
    sentenceCountRaw: metrics.length,
    sentenceCountSpeakable: metrics.filter((m) => !m.skipped).length,
    sentenceCountSkipped: metrics.filter((m) => m.skipped).length,
    totalElapsedMs: elapsed,
    ttsLatencyAvgMs: average(ttsLatencies),
    ttsLatencyP50Ms: percentile(ttsLatencies, 50),
    ttsLatencyP95Ms: percentile(ttsLatencies, 95),
    gapFromPrevSplitReadyAvgMs: average(metrics.map((m) => m.gapFromPrevSplitReadyMs)),
    gapFromPrevTtsEndAvgMs: average(metrics.map((m) => m.gapFromPrevTtsEndMs)),
    metrics,
  };

  await fs.mkdir(args.outDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const jsonPath = path.join(args.outDir, `tts-benchmark-${stamp}.json`);
  const mdPath = path.join(args.outDir, `tts-benchmark-${stamp}.md`);
  await fs.writeFile(jsonPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  await fs.writeFile(mdPath, toMd(report), "utf8");

  console.log(`[tts-benchmark] engine=${args.engine} voice=${args.voice}`);
  console.log(`[tts-benchmark] no_filter=${String(args.noFilter)} play=${String(args.play)}`);
  console.log(`[tts-benchmark] raw=${report.sentenceCountRaw} speakable=${report.sentenceCountSpeakable} skipped=${report.sentenceCountSkipped}`);
  if (report.sentenceCountSkipped > 0) {
    const skipped = report.metrics
      .filter((m) => m.skipped)
      .map((m) => `#${m.index}:${m.rawSentence}`)
      .join(" | ");
    console.log(`[tts-benchmark] skipped_sentences=${skipped}`);
  }
  console.log(`[tts-benchmark] latency_avg_ms=${report.ttsLatencyAvgMs?.toFixed(2) ?? "n/a"} p50=${report.ttsLatencyP50Ms?.toFixed(2) ?? "n/a"} p95=${report.ttsLatencyP95Ms?.toFixed(2) ?? "n/a"}`);
  console.log(`[tts-benchmark] report_json=${jsonPath}`);
  console.log(`[tts-benchmark] report_md=${mdPath}`);
  if (args.play) {
    console.log(`[tts-benchmark] audio_dir=${audioOutDir}`);
  }
}

void main().catch((e) => {
  console.error("[tts-benchmark] failed:", e);
  process.exit(1);
});
