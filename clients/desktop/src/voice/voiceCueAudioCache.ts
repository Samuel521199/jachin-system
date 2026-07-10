import manifest from "../../public/audio/companion_cues/manifest.json";
import { truncVoiceLog, voiceCompanionDebug } from "./voiceCompanionDebugLog";
import { voiceChatTraceIfActive } from "./voiceChatTraceLog";

type CueManifestItem = {
  id: string;
  text: string;
  file: string;
};

type CueManifest = {
  version: number;
  basePath: string;
  items: CueManifestItem[];
};

type CachedCue = {
  item: CueManifestItem;
  url: string;
};

const CUE_MANIFEST = manifest as CueManifest;
const cueBlobCache = new Map<string, Promise<Blob>>();

function normalizeCueText(text: string): string {
  return (text || "")
    .replace(/[\s\u3000]+/g, "")
    .replace(/[。！？!?，,、；;：:"“”‘’'（）()\[\]【】]+$/g, "")
    .trim();
}

const cueByText = new Map<string, CachedCue>();
for (const item of CUE_MANIFEST.items || []) {
  const key = normalizeCueText(item.text);
  if (!key || !item.file) continue;
  cueByText.set(key, {
    item,
    url: `${CUE_MANIFEST.basePath || "/audio/companion_cues/"}${item.file}`,
  });
}

const CUE_TEXT_ALIASES: Record<string, string> = {
  嗯: "好的",
  嗯嗯: "好的",
  好: "好的",
  好的好的: "好的",
  收到啦: "收到",
  收到了: "收到",
  我在呢: "我在",
};

export function findCompanionCueAudio(text: string): CachedCue | null {
  const key = normalizeCueText(text);
  return cueByText.get(key) ?? cueByText.get(normalizeCueText(CUE_TEXT_ALIASES[key] || "")) ?? null;
}

export async function loadCompanionCueAudio(text: string): Promise<{ blob: Blob; id: string; url: string } | null> {
  const cue = findCompanionCueAudio(text);
  if (!cue) return null;
  let pending = cueBlobCache.get(cue.url);
  if (!pending) {
    pending = fetch(cue.url)
      .then((res) => {
        if (!res.ok) throw new Error(`cue audio fetch failed: ${res.status}`);
        return res.blob();
      });
    cueBlobCache.set(cue.url, pending);
  }
  try {
    const blob = await pending;
    voiceCompanionDebug("cue_audio.cache_hit", {
      id: cue.item.id,
      text: truncVoiceLog(text, 80),
      url: cue.url,
      bytes: blob.size,
    });
    voiceChatTraceIfActive("tts.cue_cache_hit", {
      id: cue.item.id,
      text: truncVoiceLog(text, 120),
      url: cue.url,
      bytes: blob.size,
    });
    return { blob, id: cue.item.id, url: cue.url };
  } catch (e) {
    cueBlobCache.delete(cue.url);
    voiceCompanionDebug("cue_audio.cache_fail", {
      id: cue.item.id,
      text: truncVoiceLog(text, 80),
      url: cue.url,
      err: String(e),
    });
    voiceChatTraceIfActive("tts.cue_cache_fail", {
      id: cue.item.id,
      text: truncVoiceLog(text, 120),
      url: cue.url,
      err: String(e),
    });
    return null;
  }
}
