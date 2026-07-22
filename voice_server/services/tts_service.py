from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    from defaults import DEFAULT_KOKORO_TTS_CUE_STYLE_INDEX, DEFAULT_KOKORO_TTS_SPEED, DEFAULT_KOKORO_TTS_VOICE
except ModuleNotFoundError:
    from voice_server.defaults import DEFAULT_KOKORO_TTS_CUE_STYLE_INDEX, DEFAULT_KOKORO_TTS_SPEED, DEFAULT_KOKORO_TTS_VOICE
from typing import Any

import numpy as np
import onnxruntime as ort

logger = logging.getLogger("jachin.voice_server.tts")

DEFAULT_SAMPLE_RATE = 24000
DEFAULT_MODEL_DIRNAME = "Kokoro-82M-v1.1-zh-ONNX"
DEFAULT_CPU_THREADS = 4
AUTO_PAUSE_MIN_HAN_CHARS = 24
AUTO_PAUSE_SYLLABLES = 18
TRIM_WINDOW_MS = 10
CUE_KEEP_LEADING_SILENCE_MS = 120
CUE_KEEP_TRAILING_SILENCE_MS = 220
CONTENT_KEEP_LEADING_SILENCE_MS = 180
CONTENT_KEEP_TRAILING_SILENCE_MS = 320
WEAK_BOUNDARY_AFTER_WORDS = {"记得", "请", "帮我", "然后", "但是", "不过", "所以", "另外", "还有", "顺便"}
# Kokoro-zh 路径固定使用中文 G2P。
KOKORO_LANGUAGE_CODE = "z"
TONE_LIKE_CHAR_SET = {
    "↓", "↑", "↘", "↗", "→",
    "˥", "˦", "˧", "˨", "˩",
}

# 将 G2P 常见声调序列转换为 tokenizer 认识的数字声调 token。
PHONEME_SEQUENCE_MAP: dict[str, str] = {
    # misaki pinyin_to_ipa tone bars: ma1/2/3/4 -> ma˥ / ma˧˥ / ma˧˩˧ / ma˥˩
    "˧˩˧": "3",
    "˨˩˦": "3",
    "˧˥": "2",
    "˥˩": "4",
    "˥": "1",
}

# 将 G2P 常见「非 vocab 字符」转换为 tokenizer 可接受字符，避免 OOV 静默吞字。
PHONEME_CHAR_MAP: dict[str, str] = {
    # Tone arrows / fallback tone bars -> Kokoro zh tokenizer tone-number tokens.
    "→": "1",
    "↗": "2",
    "↑": "2",
    "↓": "3",
    "↘": "4",
    "˥": "1",
    "˦": "1",
    "˧": "5",
    "˨": "3",
    "˩": "3",
    # IPA variants from different zh G2P engines
    "ɚ": "ə",
    "ɝ": "ə",
    "ʅ": "ɨ",
    "ʮ": "ɨ",
    "ʯ": "ɨ",
    "ʐ": "ɹ",
    "ɻ": "ɹ",
    "ɥ": "y",
    "ɤ": "ə",
    # combining marks / rare symbols from IPA output
    "̯": "",
    "̩": "",
    "ꭧ": "ʧ",
    # Punctuation. Commas are softened to a light boundary; sentence endings stay explicit.
    "，": " ",
    ",": " ",
    "、": " ",
    "。": ".",
    # Tie bar
    "͡": "",
}


@dataclass
class TtsResult:
    wav_bytes: bytes
    duration_ms: int
    sample_rate: int
    synth_ms: int = 0
    attempts: int = 1
    max_new_frames: int = 0
    quality_status: str = "ok"
    trace: dict[str, Any] | None = None


class TtsCancelledError(RuntimeError):
    pass


class TtsService:
    """Kokoro ONNX local synthesis service."""

    def __init__(
        self,
        tts_dir: Path,
        default_voice: str = DEFAULT_KOKORO_TTS_VOICE,
        default_speed: float = DEFAULT_KOKORO_TTS_SPEED,
    ) -> None:
        self.tts_dir = tts_dir
        self.default_voice = default_voice.strip() or DEFAULT_KOKORO_TTS_VOICE
        self.default_speed = float(np.clip(default_speed, 0.8, 1.5))
        self._session: ort.InferenceSession | None = None
        self._load_error: str | None = None
        self._runtime_lock = threading.Lock()
        self._synthesize_lock = threading.Lock()
        self._cancel_lock = threading.Lock()
        self._global_cancel_seq = 0
        self._session_cancel_seq: dict[str, int] = {}
        self._voices_cache: list[str] | None = None
        self._g2p: Any = None
        self._g2p_name = ""
        self._tokenizer: Any = None
        self._tokenizer_load_attempted = False
        self._tokenizer_vocab: dict[str, int] | None = None
        self._model_sha = ""
        self._jieba = None
        self._lazy_pinyin = None
        self._style_tone3 = None
        self._misaki_zh = None
        self._style_mode = str(os.getenv("JACHIN_VOICE_TTS_STYLE_MODE", "token_len")).strip().lower() or "token_len"
        self._style_index_override = self._parse_optional_int_env("JACHIN_VOICE_TTS_STYLE_INDEX")
        cue_style_index = self._parse_optional_int_env("JACHIN_VOICE_TTS_CUE_STYLE_INDEX")
        self._cue_style_index = DEFAULT_KOKORO_TTS_CUE_STYLE_INDEX if cue_style_index is None else cue_style_index
        self._zh_frontend_mode = str(os.getenv("JACHIN_VOICE_TTS_ZH_FRONTEND_MODE", "auto")).strip().lower() or "auto"
        if self._zh_frontend_mode not in {"auto", "on", "off"}:
            logger.warning("Invalid JACHIN_VOICE_TTS_ZH_FRONTEND_MODE=%r, fallback to auto", self._zh_frontend_mode)
            self._zh_frontend_mode = "auto"
        self._resolve_model_paths()

    def _resolve_model_paths(self) -> None:
        # 兼容两种传法：tts 根目录或 Kokoro 子目录
        if (self.tts_dir / "onnx" / "model.onnx").is_file():
            self.kokoro_dir = self.tts_dir
        else:
            self.kokoro_dir = self.tts_dir / DEFAULT_MODEL_DIRNAME
        self.model_path = self.kokoro_dir / "onnx" / "model.onnx"
        self.voices_dir = self.kokoro_dir / "voices"

    @property
    def ready(self) -> bool:
        return self.model_path.is_file() and self.voices_dir.is_dir()

    def _load_engine(self) -> bool:
        if self._session is not None:
            return True
        if self._load_error is not None:
            return False
        if not self.ready:
            self._load_error = f"Kokoro model files missing under: {self.kokoro_dir}"
            return False
        try:
            with self._runtime_lock:
                if self._session is not None:
                    return True
                execution_provider = "CPUExecutionProvider"
                ep_env = str(os.getenv("JACHIN_VOICE_TTS_EP", "auto")).strip().lower()
                providers = ort.get_available_providers()
                if ep_env in {"auto", "cuda", "gpu"} and "CUDAExecutionProvider" in providers:
                    execution_provider = "CUDAExecutionProvider"
                sess_opts = ort.SessionOptions()
                raw_threads = str(os.getenv("JACHIN_VOICE_TTS_THREADS", str(DEFAULT_CPU_THREADS))).strip()
                try:
                    sess_opts.intra_op_num_threads = max(1, int(raw_threads))
                except Exception:
                    sess_opts.intra_op_num_threads = DEFAULT_CPU_THREADS
                logger.info(
                    "Loading Kokoro ONNX runtime from %s (ep=%s, threads=%s)",
                    self.model_path,
                    execution_provider,
                    sess_opts.intra_op_num_threads,
                )
                self._session = ort.InferenceSession(
                    str(self.model_path),
                    sess_options=sess_opts,
                    providers=[execution_provider],
                )
                self._model_sha = self._file_sha256_short(self.model_path, limit_bytes=8 * 1024 * 1024)
                logger.info(
                    "Kokoro model fingerprint model=%s model_sha=%s tokenizer=%s voices_dir=%s sample_rate=%s",
                    self.model_path,
                    self._model_sha,
                    self.kokoro_dir / "tokenizer.json",
                    self.voices_dir,
                    DEFAULT_SAMPLE_RATE,
                )
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.exception("Kokoro ONNX load failed: %s", e)
            return False

    @staticmethod
    def _parse_optional_int_env(name: str) -> int | None:
        raw = str(os.getenv(name, "")).strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            logger.warning("Invalid integer env %s=%r, ignoring", name, raw)
            return None

    @staticmethod
    def _resolve_tts_kind(kind: str | None, normalized_text: str) -> str:
        requested = (kind or "").strip().lower()
        if requested in {"cue", "content"}:
            return requested
        compact = re.sub(r"\s+", "", normalized_text or "")
        if compact in {"我在。", "我想想。", "收到，我来处理。", "你好，我在。", "我在，怎么了？", "你好，有什么可以帮你。", "你好，有什么可以帮您。"}:
            return "cue"
        return "content"

    @staticmethod
    def _file_sha256_short(path: Path, limit_bytes: int | None = None) -> str:
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                if limit_bytes is None:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        if not chunk:
                            break
                        h.update(chunk)
                else:
                    h.update(f.read(max(0, limit_bytes)))
            return h.hexdigest()[:16]
        except Exception:
            return ""

    def _ensure_g2p(self) -> Any:
        if self._g2p is not None:
            return self._g2p
        try:
            from misaki.zh import ZHG2P

            self._g2p = ZHG2P()
            self._g2p_name = "misaki.zh.ZHG2P"
            logger.info("Kokoro G2P locked to Chinese path (lang=%s, backend=%s)", KOKORO_LANGUAGE_CODE, self._g2p_name)
            return self._g2p
        except Exception as e:
            from misaki import z

            self._g2p = z.G2P()
            self._g2p_name = "misaki.z.G2P"
            logger.warning("Kokoro G2P fallback to %s (lang=%s): %s", self._g2p_name, KOKORO_LANGUAGE_CODE, e)
        return self._g2p

    def _ensure_zh_frontend_deps(self) -> bool:
        if self._jieba is not None and self._lazy_pinyin is not None and self._style_tone3 is not None and self._misaki_zh is not None:
            return True
        try:
            import jieba  # type: ignore
            import misaki.zh as misaki_zh  # type: ignore
            from pypinyin import Style, lazy_pinyin  # type: ignore

            self._jieba = jieba
            self._lazy_pinyin = lazy_pinyin
            self._style_tone3 = Style.TONE3
            self._misaki_zh = misaki_zh
            return True
        except Exception as e:
            logger.warning("Zh frontend deps unavailable, fallback to baseline G2P: %s", e)
            return False

    @staticmethod
    def _is_han(ch: str) -> bool:
        return "\u4e00" <= ch <= "\u9fff"

    @staticmethod
    def _is_punc(ch: str) -> bool:
        return ch in "，。！？；,.!?;:：、"

    @staticmethod
    def _frontend_pause_for_punc(ch: str) -> str:
        if ch in "，,、":
            return " "
        if ch in "；;:：":
            return " "
        if ch in "。.!?！？":
            return ch
        return ""

    @staticmethod
    def _tone_num(py: str) -> int:
        m = re.search(r"([1-5])$", py or "")
        return int(m.group(1)) if m else 5

    @staticmethod
    def _replace_tone(py: str, tone: int) -> str:
        base = re.sub(r"[1-5]$", "", py or "")
        return f"{base}{int(tone)}"

    def _apply_tone_sandhi_rules(self, chars: list[str], pinyins: list[str]) -> list[str]:
        if not chars or not pinyins or len(chars) != len(pinyins):
            return pinyins
        out = list(pinyins)

        # Rule 1: third-tone sandhi: 3-3 => 2-3 (left one changes)
        tones = [self._tone_num(py) for py in out]
        i = 0
        while i < len(out) - 1:
            if tones[i] == 3 and tones[i + 1] == 3:
                out[i] = self._replace_tone(out[i], 2)
                tones[i] = 2
            i += 1

        # Rule 2: "一" tone change
        for i, ch in enumerate(chars):
            if ch != "一":
                continue
            if i + 1 < len(out):
                nxt = self._tone_num(out[i + 1])
                out[i] = self._replace_tone(out[i], 2 if nxt == 4 else 4)
            else:
                out[i] = self._replace_tone(out[i], 1)

        # Rule 3: "不" tone change
        for i, ch in enumerate(chars):
            if ch != "不":
                continue
            if i + 1 < len(out):
                nxt = self._tone_num(out[i + 1])
                out[i] = self._replace_tone(out[i], 2 if nxt == 4 else 4)
            else:
                out[i] = self._replace_tone(out[i], 4)

        return out

    def _pinyin_to_ipa_syllable(self, py: str) -> str:
        if self._misaki_zh is None:
            return ""
        try:
            ipa_set = self._misaki_zh.pinyin_to_ipa(py)
            if not ipa_set:
                return ""
            first = next(iter(ipa_set))
            if isinstance(first, tuple):
                return "".join(str(x) for x in first if x)
            return str(first)
        except Exception:
            return ""

    def _build_phonemes_with_zh_frontend(self, text: str) -> tuple[str, str]:
        """
        Chinese text frontend:
        - jieba segmentation
        - pypinyin tone_sandhi + explicit 3rd-tone / yi / bu rules
        - pinyin -> IPA via misaki.zh.pinyin_to_ipa
        - prosody pauses by punctuation + long-run comma insertion
        """
        if self._zh_frontend_mode == "off":
            return "", "zh_frontend_disabled"
        if not self._ensure_zh_frontend_deps():
            return "", "zh_frontend_missing_deps"

        assert self._jieba is not None and self._lazy_pinyin is not None and self._style_tone3 is not None

        segments = [seg for seg in self._jieba.lcut(text, cut_all=False) if seg]
        if not segments:
            return "", "zh_frontend_empty_segments"

        parts: list[str] = []
        run_syllables = 0
        han_count = sum(1 for ch in text if self._is_han(ch))
        has_original_pause = any(self._is_punc(ch) for ch in text)
        auto_pause_enabled = han_count >= AUTO_PAUSE_MIN_HAN_CHARS and not has_original_pause
        for seg in segments:
            # Keep punctuation, but use a light boundary for commas in conversational speech.
            if all(self._is_punc(ch) for ch in seg):
                for ch in seg:
                    pause = self._frontend_pause_for_punc(ch)
                    if not pause:
                        continue
                    if pause == " " and (not parts or parts[-1] == " "):
                        continue
                    parts.append(pause)
                run_syllables = 0
                continue

            chars = list(seg)
            pys = self._lazy_pinyin(
                seg,
                style=self._style_tone3,
                neutral_tone_with_five=True,
                tone_sandhi=True,
                strict=False,
                errors="default",
            )
            if len(pys) != len(chars):
                # mixed token: fallback char-wise split
                pys = self._lazy_pinyin(
                    "".join(ch for ch in chars),
                    style=self._style_tone3,
                    neutral_tone_with_five=True,
                    tone_sandhi=True,
                    strict=False,
                    errors="default",
                )
            if len(pys) != len(chars):
                continue

            pys = self._apply_tone_sandhi_rules(chars, list(pys))
            local_syllables = 0
            for py in pys:
                ipa = self._pinyin_to_ipa_syllable(py)
                if not ipa:
                    continue
                parts.append(ipa)
                local_syllables += 1
                run_syllables += 1
                # Prosody: only add a light pause for long, punctuation-free utterances.
                if auto_pause_enabled and run_syllables >= AUTO_PAUSE_SYLLABLES:
                    if parts and parts[-1] != " ":
                        parts.append(" ")
                    run_syllables = 0

            if seg in WEAK_BOUNDARY_AFTER_WORDS and parts and parts[-1] != " ":
                parts.append(" ")

        phonemes = "".join(parts).strip()
        return phonemes, "jieba+pypinyin+misaki_ipa"

    @staticmethod
    def _normalize_text_for_zh_tts(text: str) -> str:
        out = (text or "").strip()
        if not out:
            return " "

        # Remove emoji/pictographs but keep Chinese punctuation for zh prosody.
        out = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", "", out)
        out = out.replace("\u3000", " ")
        out = re.sub(r"\s+", " ", out).strip()

        # Stabilize frequent mixed-language snippets before Chinese G2P.
        out = re.sub(r"(?i)(?<![A-Za-z])jachin(?![A-Za-z])", "\u5609\u79e6", out)
        out = re.sub(r"(?i)(?<![A-Za-z])a\s*i(?![A-Za-z])", "A I", out)
        out = re.sub(r"(?i)(?<![A-Za-z])m\s*d(?![A-Za-z])", "M D", out)
        out = re.sub(r"\b([A-Za-z])\s*\u76d8\b", lambda m: f"{m.group(1).upper()} \u76d8", out)

        # Convert numbers, with years read digit-by-digit: 2026年 -> 二零二六年.
        cn_digits = "\u96f6\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d"

        def _year_to_cn(m: re.Match[str]) -> str:
            return "".join(cn_digits[int(ch)] for ch in m.group(1)) + "\u5e74"

        out = re.sub(r"(?<!\d)(\d{4})\s*\u5e74", _year_to_cn, out)

        try:
            import cn2an  # type: ignore

            def _num_to_cn(m: re.Match[str]) -> str:
                s = m.group(0)
                if "." in s:
                    return s
                try:
                    return str(cn2an.an2cn(int(s), "low"))
                except Exception:
                    return s

            out = re.sub(r"\d+(?:\.\d+)?", _num_to_cn, out)
        except Exception:
            pass


        if out and any("\u4e00" <= ch <= "\u9fff" for ch in out) and not re.search(r"[。！？!?；;.]$", out):
            out = out.rstrip("，,、；;:：") + "。"

        return out or " "

    def _load_tokenizer(self) -> Any | None:
        if self._tokenizer is not None:
            return self._tokenizer
        if self._tokenizer_load_attempted:
            return None
        self._tokenizer_load_attempted = True
        tokenizer_path = self.kokoro_dir / "tokenizer.json"
        try:
            from tokenizers import Tokenizer

            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            logger.info("Kokoro tokenizer loaded from %s", tokenizer_path)
        except Exception as e:
            logger.warning("Kokoro tokenizer load failed, falling back to vocab lookup: %s", e)
            self._tokenizer = None
        return self._tokenizer

    def _load_tokenizer_vocab(self) -> dict[str, int]:
        if self._tokenizer_vocab is not None:
            return self._tokenizer_vocab
        tokenizer_path = self.kokoro_dir / "tokenizer.json"
        data = json.loads(tokenizer_path.read_text(encoding="utf-8"))
        vocab = data.get("model", {}).get("vocab", {})
        if not isinstance(vocab, dict) or not vocab:
            raise RuntimeError(f"invalid tokenizer vocab: {tokenizer_path}")
        self._tokenizer_vocab = {str(k): int(v) for k, v in vocab.items()}
        return self._tokenizer_vocab

    def _phonemes_to_tokens_by_vocab(self, phonemes: str, *, return_meta: bool = False) -> list[int] | tuple[list[int], dict[str, Any]]:
        vocab = self._load_tokenizer_vocab()
        sequence_mapped = 0
        normalized_phonemes = phonemes
        for src, dst in sorted(PHONEME_SEQUENCE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            count = normalized_phonemes.count(src)
            if count:
                normalized_phonemes = normalized_phonemes.replace(src, dst)
                sequence_mapped += count
        normalized_chars: list[str] = []
        mapped_count = 0
        map_drop_counts: dict[str, int] = {}
        for ch in normalized_phonemes:
            mapped = PHONEME_CHAR_MAP.get(ch, ch)
            if mapped != ch:
                mapped_count += 1
            if not mapped:
                map_drop_counts[ch] = map_drop_counts.get(ch, 0) + 1
                continue
            normalized_chars.append(mapped)
        normalized = "".join(normalized_chars)
        if mapped_count > 0:
            logger.info(
                "Kokoro phoneme map applied: sequence_mapped=%s changed=%s raw_len=%s norm_len=%s",
                sequence_mapped,
                mapped_count,
                len(phonemes),
                len(normalized),
            )

        out: list[int] = []
        oov_counts: dict[str, int] = {}
        for ch in normalized:
            token = vocab.get(ch)
            if token is not None:
                out.append(token)
            else:
                oov_counts[ch] = oov_counts.get(ch, 0) + 1
        for ch, count in sorted(oov_counts.items(), key=lambda x: (-x[1], x[0])):
            logger.warning("OOV character dropped: %r (U+%04X) count=%s", ch, ord(ch), count)
        if not return_meta:
            return out
        tone_drop_count = 0
        for ch, cnt in map_drop_counts.items():
            if ch in TONE_LIKE_CHAR_SET:
                tone_drop_count += cnt
        for ch, cnt in oov_counts.items():
            if ch in TONE_LIKE_CHAR_SET:
                tone_drop_count += cnt
        meta = {
            "raw_len": len(phonemes),
            "normalized_len": len(normalized),
            "token_len": len(out),
            "normalized_phonemes": normalized,
            "pause_score": normalized.count(" ") + (normalized.count(",") + normalized.count("，")) * 2,
            "space_count": normalized.count(" "),
            "comma_count": normalized.count(",") + normalized.count("，"),
            "sequence_mapped": sequence_mapped,
            "mapped_changes": mapped_count,
            "map_drop_counts": map_drop_counts,
            "oov_after_map_counts": oov_counts,
            "total_drop": sum(map_drop_counts.values()) + sum(oov_counts.values()),
            "tone_drop_count": tone_drop_count,
        }
        return out, meta

    def _phonemes_to_tokens_with_source(self, phonemes: str) -> tuple[list[int], str]:
        # The bundled Kokoro tokenizer.json is a vocab-style file whose post_processor
        # is not accepted by every tokenizers build. The old local path used the same
        # character-level vocab mapping, so keep that deterministic route here.
        return self._phonemes_to_tokens_by_vocab(phonemes), "tokenizer_json_vocab"

    def _phonemes_to_tokens(self, phonemes: str) -> list[int]:
        return self._phonemes_to_tokens_with_source(phonemes)[0]

    @staticmethod
    def _coerce_g2p_tokens(tokens: Any) -> list[int]:
        out: list[int] = []
        for item in tokens or []:
            try:
                out.append(int(item))
            except Exception:
                return []
        return out

    def list_voices(self) -> list[str]:
        if self._voices_cache is not None:
            return list(self._voices_cache)
        if not self.voices_dir.is_dir():
            return [self.default_voice]
        out = sorted(p.stem for p in self.voices_dir.glob("*.bin") if p.is_file())
        voices = out if out else [self.default_voice]
        self._voices_cache = voices
        return list(voices)

    def has_voice(self, voice: str | None) -> bool:
        voice_id = (voice or "").strip()
        if not voice_id:
            return False
        return voice_id in self.list_voices()

    def cancel_session(self, session_id: str | None) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        with self._cancel_lock:
            self._session_cancel_seq[sid] = self._session_cancel_seq.get(sid, 0) + 1
        return True

    def cancel_all(self) -> None:
        with self._cancel_lock:
            self._global_cancel_seq += 1

    def _snapshot_cancel_state(self, session_id: str | None) -> tuple[int, int]:
        sid = (session_id or "").strip()
        with self._cancel_lock:
            return (
                self._global_cancel_seq,
                self._session_cancel_seq.get(sid, 0) if sid else 0,
            )

    def _is_cancelled(self, session_id: str | None, snapshot: tuple[int, int]) -> bool:
        sid = (session_id or "").strip()
        with self._cancel_lock:
            if self._global_cancel_seq != snapshot[0]:
                return True
            if sid and self._session_cancel_seq.get(sid, 0) != snapshot[1]:
                return True
        return False

    def _ensure_not_cancelled(self, session_id: str | None, snapshot: tuple[int, int], stage: str) -> None:
        if self._is_cancelled(session_id, snapshot):
            raise TtsCancelledError(f"tts_cancelled_at:{stage}")

    def synthesize(self, text: str, voice: str | None = None, session_id: str | None = None, speed: float | None = None, kind: str | None = None) -> TtsResult:
        normalized = self._normalize_text_for_zh_tts(text)
        tts_kind = self._resolve_tts_kind(kind, normalized)

        if not self.ready:
            return self._fallback_sine(normalized, freq_hz=330.0, sample_rate=DEFAULT_SAMPLE_RATE)
        if not self._load_engine():
            return self._error_wav(f"[TTS ERROR] Kokoro model not loaded: {self._load_error or 'unknown'}")

        speed_value = self.default_speed if speed is None else float(np.clip(speed, 0.8, 1.5))
        voice_id = (voice or self.default_voice).strip() or self.default_voice
        if not self.has_voice(voice_id):
            fallback_voice = self.default_voice if self.has_voice(self.default_voice) else (self.list_voices()[0])
            logger.warning("Kokoro voice '%s' missing, fallback to '%s'", voice_id, fallback_voice)
            voice_id = fallback_voice

        voice_sha = self._file_sha256_short(self.voices_dir / f"{voice_id}.bin", limit_bytes=1024 * 1024)
        logger.info("Kokoro voice selected voice=%s voice_sha=%s model_sha=%s", voice_id, voice_sha, self._model_sha)

        cancel_snapshot = self._snapshot_cancel_state(session_id)
        started_at = time.perf_counter()
        self._ensure_not_cancelled(session_id, cancel_snapshot, "before_synthesize")
        try:
            result = self._synthesize_once(normalized, voice_id, session_id, cancel_snapshot, speed_value, tts_kind)
            result.synth_ms = int((time.perf_counter() - started_at) * 1000)
            return result
        except TtsCancelledError:
            raise
        except Exception as e:
            logger.exception("Kokoro synthesize failed")
            return self._error_wav(f"[TTS ERROR] Kokoro synthesize failed: {e}")

    def _synthesize_once(
        self,
        text: str,
        voice_id: str,
        session_id: str | None,
        cancel_snapshot: tuple[int, int],
        speed: float,
        tts_kind: str,
    ) -> TtsResult:
        self._ensure_not_cancelled(session_id, cancel_snapshot, "before_phonemize")
        token_ids: list[int] = []
        token_source = ""
        phonemes: Any = ""
        g2p = self._ensure_g2p()

        candidates: list[dict[str, Any]] = []
        candidate_debug: list[dict[str, Any]] = []
        chosen_name = ""
        chosen_meta: dict[str, Any] = {}

        # Candidate A: baseline G2P route
        base_phonemes, base_tokens_raw = g2p(text)
        base_token_ids = self._coerce_g2p_tokens(base_tokens_raw)
        if base_token_ids:
            candidates.append(
                {
                    "name": "baseline_g2p_tokens",
                    "phonemes": base_phonemes,
                    "token_ids": base_token_ids,
                    "token_source": "g2p_tokens",
                    "meta": {"total_drop": 0, "tone_drop_count": 0, "token_len": len(base_token_ids)},
                }
            )
        elif isinstance(base_phonemes, str):
            base_token_ids2, base_meta = self._phonemes_to_tokens_by_vocab(base_phonemes, return_meta=True)  # type: ignore[assignment]
            if base_token_ids2:
                candidates.append(
                    {
                        "name": "baseline_vocab_map",
                        "phonemes": base_phonemes,
                        "token_ids": base_token_ids2,
                        "token_source": "tokenizer_json_vocab",
                        "meta": base_meta,
                    }
                )

        # Candidate B: zh frontend route (optional)
        if self._zh_frontend_mode != "off":
            zh_phonemes, zh_source = self._build_phonemes_with_zh_frontend(text)
            if zh_phonemes:
                zh_token_ids, zh_meta = self._phonemes_to_tokens_by_vocab(zh_phonemes, return_meta=True)  # type: ignore[assignment]
                if zh_token_ids:
                    candidates.append(
                        {
                            "name": "zh_frontend_vocab_map",
                            "phonemes": zh_phonemes,
                            "token_ids": zh_token_ids,
                            "token_source": f"tokenizer_json_vocab+{zh_source}",
                            "meta": zh_meta,
                        }
                    )

        if candidates:
            if self._zh_frontend_mode == "on":
                # Prefer zh frontend when explicitly requested.
                chosen = next((c for c in candidates if c["name"] == "zh_frontend_vocab_map"), candidates[0])
            elif self._zh_frontend_mode == "off":
                chosen = candidates[0]
            else:
                # auto: keep tones first, then prefer the candidate with fewer pause tokens.
                chosen = sorted(
                    candidates,
                    key=lambda c: (
                        int(c.get("meta", {}).get("tone_drop_count", 0)),
                        int(c.get("meta", {}).get("pause_score", 9999)),
                        int(c.get("meta", {}).get("total_drop", 0)),
                        -int(c.get("meta", {}).get("token_len", len(c["token_ids"]))),
                    ),
                )[0]
            phonemes = chosen["phonemes"]
            token_ids = list(chosen["token_ids"])
            token_source = str(chosen["token_source"])
            chosen_name = str(chosen["name"])
            chosen_meta = dict(chosen.get("meta", {}))
            candidate_debug = [
                {
                    "name": c["name"],
                    "token_len": int(c.get("meta", {}).get("token_len", len(c["token_ids"]))),
                    "tone_drop": int(c.get("meta", {}).get("tone_drop_count", 0)),
                    "total_drop": int(c.get("meta", {}).get("total_drop", 0)),
                    "sequence_mapped": int(c.get("meta", {}).get("sequence_mapped", 0)),
                    "pause_score": int(c.get("meta", {}).get("pause_score", 0)),
                    "space_count": int(c.get("meta", {}).get("space_count", 0)),
                }
                for c in candidates
            ]
            logger.info(
                "Kokoro frontend choose=%s mode=%s candidates=%s",
                chosen_name,
                self._zh_frontend_mode,
                candidate_debug,
            )
        if not token_ids:
            raise RuntimeError("phonemizer returned empty tokens")
        if len(token_ids) > 510:
            raise RuntimeError(f"token sequence too long: {len(token_ids)} (max 510)")

        style_index_override = self._style_index_override
        style_mode = self._style_mode
        if tts_kind == "cue" and style_index_override is None:
            style_index_override = self._cue_style_index
        style_mode_label = style_mode if style_index_override is None else f"fixed:{style_index_override}"
        style_vec, style_idx, style_count = self._select_style_vector(
            voice_id,
            len(token_ids),
            len(phonemes) if isinstance(phonemes, str) else 0,
            style_mode=style_mode,
            style_index_override=style_index_override,
        )
        logger.info(
            "Kokoro TTS prepared voice=%s speed=%.2f g2p=%s token_source=%s token_len=%s phoneme_len=%s style=%s/%s style_mode=%s sample_rate=%s text=%r",
            voice_id,
            speed,
            self._g2p_name or type(g2p).__name__,
            token_source,
            len(token_ids),
            len(phonemes) if isinstance(phonemes, str) else 0,
            style_idx,
            max(0, style_count - 1),
            style_mode_label,
            DEFAULT_SAMPLE_RATE,
            text,
        )
        # Kokoro ONNX 使用 [0] + token_ids + [0] 作为输入。
        input_ids = [0, *token_ids, 0]
        self._ensure_not_cancelled(session_id, cancel_snapshot, "before_inference")
        with self._synthesize_lock:
            if self._session is None:
                raise RuntimeError("onnx session not initialized")
            outputs = self._session.run(
                None,
                {
                    "input_ids": np.asarray([input_ids], dtype=np.int64),
                    "style": np.asarray([style_vec], dtype=np.float32),
                    "speed": np.asarray([speed], dtype=np.float32),
                },
            )
        self._ensure_not_cancelled(session_id, cancel_snapshot, "after_inference")

        if not outputs:
            raise RuntimeError("onnx returned empty outputs")
        samples = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        samples, audio_trim = self._trim_silence(samples, DEFAULT_SAMPLE_RATE, tts_kind)
        wav_bytes = self._pcm_f32_to_wav(samples, DEFAULT_SAMPLE_RATE)
        duration_ms = self._wav_duration_ms(wav_bytes, sample_rate=DEFAULT_SAMPLE_RATE)
        phoneme_text = phonemes if isinstance(phonemes, str) else str(phonemes)
        mapped_phoneme_text = str(chosen_meta.get("normalized_phonemes", phoneme_text))
        pause_stats = {
            "space_count": mapped_phoneme_text.count(" "),
            "comma_count": mapped_phoneme_text.count(",") + mapped_phoneme_text.count("，"),
            "period_count": mapped_phoneme_text.count(".") + mapped_phoneme_text.count("。"),
            "original_punctuation_count": sum(1 for ch in text if self._is_punc(ch)),
            "inserted_comma_count": max(0, mapped_phoneme_text.count("，") - text.count("，")),
        }
        return TtsResult(
            wav_bytes=wav_bytes,
            duration_ms=duration_ms,
            sample_rate=DEFAULT_SAMPLE_RATE,
            trace={
                "actual_path": chosen_name,
                "token_source": token_source,
                "token_count": len(token_ids),
                "input_ids_count": len(input_ids),
                "input_ids": input_ids,
                "phonemes": phoneme_text,
                "mapped_phonemes": mapped_phoneme_text,
                "pause_stats": pause_stats,
                "tone_drop_count": int(chosen_meta.get("tone_drop_count", 0)),
                "total_drop": int(chosen_meta.get("total_drop", 0)),
                "sequence_mapped": int(chosen_meta.get("sequence_mapped", 0)),
                "mapping_summary": chosen_meta,
                "candidates": candidate_debug,
                "style_index": style_idx,
                "style_count": style_count,
                "style_mode": style_mode_label,
                "tts_kind": tts_kind,
                "audio_trim": audio_trim,
                "sample_rate": DEFAULT_SAMPLE_RATE,
            },
        )

    def _select_style_vector(
        self,
        voice_id: str,
        token_len: int,
        phoneme_len: int = 0,
        *,
        style_mode: str | None = None,
        style_index_override: int | None = None,
    ) -> tuple[np.ndarray, int, int]:
        style_path = self.voices_dir / f"{voice_id}.bin"
        if not style_path.is_file():
            raise RuntimeError(f"voice style file not found: {style_path}")
        raw = style_path.read_bytes()
        if len(raw) % 4 != 0:
            raise RuntimeError(f"invalid voice bin size: {style_path}")
        floats = np.frombuffer(raw, dtype="<f4")
        if floats.size % 256 != 0:
            raise RuntimeError(f"invalid voice bin vector width (expect 256): {style_path}")
        vectors = floats.reshape(-1, 256)
        max_idx = max(0, vectors.shape[0] - 1)
        selected_mode = (style_mode or self._style_mode).strip().lower() or "token_len"
        if style_index_override is not None:
            style_idx = int(np.clip(style_index_override, 0, max_idx))
        elif selected_mode == "zero":
            style_idx = 0
        elif selected_mode == "phoneme_len":
            style_idx = min(max(0, phoneme_len), max_idx)
        else:
            # Official Kokoro-style lookup: style vector row follows token length.
            style_idx = min(max(0, token_len), max_idx)
        return vectors[style_idx], style_idx, vectors.shape[0]

    @staticmethod
    def _trim_silence(samples: np.ndarray, sample_rate: int, tts_kind: str) -> tuple[np.ndarray, dict[str, Any]]:
        original_len = int(samples.size)
        original_ms = int(original_len / max(1, sample_rate) * 1000)
        empty_stats: dict[str, Any] = {
            "applied": False,
            "original_duration_ms": original_ms,
            "duration_ms": original_ms,
            "leading_trim_ms": 0,
            "trailing_trim_ms": 0,
            "threshold": 0.0,
            "peak": 0.0,
        }
        if original_len <= 0:
            return samples, empty_stats

        abs_samples = np.abs(samples.astype(np.float32, copy=False))
        peak = float(np.max(abs_samples)) if abs_samples.size else 0.0
        if peak <= 0.0:
            empty_stats["peak"] = 0.0
            return samples, empty_stats

        threshold = max(0.004, peak * 0.02)
        window = max(1, int(sample_rate * TRIM_WINDOW_MS / 1000))
        active_windows: list[int] = []
        for start in range(0, original_len, window):
            chunk = abs_samples[start : min(original_len, start + window)]
            if chunk.size == 0:
                continue
            rms = float(np.sqrt(np.mean(np.square(chunk))))
            if rms >= threshold:
                active_windows.append(start // window)

        if not active_windows:
            empty_stats.update({"threshold": round(threshold, 6), "peak": round(peak, 6)})
            return samples, empty_stats

        keep_lead_ms = CUE_KEEP_LEADING_SILENCE_MS if tts_kind == "cue" else CONTENT_KEEP_LEADING_SILENCE_MS
        keep_tail_ms = CUE_KEEP_TRAILING_SILENCE_MS if tts_kind == "cue" else CONTENT_KEEP_TRAILING_SILENCE_MS
        keep_lead = int(sample_rate * keep_lead_ms / 1000)
        keep_tail = int(sample_rate * keep_tail_ms / 1000)
        active_start = active_windows[0] * window
        active_end = min(original_len, (active_windows[-1] + 1) * window)
        trim_start = max(0, active_start - keep_lead)
        trim_end = min(original_len, active_end + keep_tail)
        if trim_start >= trim_end:
            empty_stats.update({"threshold": round(threshold, 6), "peak": round(peak, 6)})
            return samples, empty_stats

        trimmed = samples[trim_start:trim_end].copy()
        duration_ms = int(trimmed.size / max(1, sample_rate) * 1000)
        leading_trim_ms = int(trim_start / max(1, sample_rate) * 1000)
        trailing_trim_ms = int((original_len - trim_end) / max(1, sample_rate) * 1000)
        stats = {
            "applied": bool(leading_trim_ms > 0 or trailing_trim_ms > 0),
            "original_duration_ms": original_ms,
            "duration_ms": duration_ms,
            "leading_trim_ms": leading_trim_ms,
            "trailing_trim_ms": trailing_trim_ms,
            "threshold": round(threshold, 6),
            "peak": round(peak, 6),
            "keep_leading_ms": keep_lead_ms,
            "keep_trailing_ms": keep_tail_ms,
        }
        return trimmed, stats

    @staticmethod
    def _wav_duration_ms(wav_bytes: bytes, sample_rate: int) -> int:
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                frames = wf.getnframes()
                sr = wf.getframerate() or sample_rate
                return int(frames / max(1, sr) * 1000)
        except Exception:
            return 0

    @staticmethod
    def _pcm_f32_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16.tobytes())
        return buf.getvalue()

    def _fallback_sine(self, text: str, freq_hz: float, sample_rate: int) -> TtsResult:
        duration_ms = min(8000, max(300, len(text) * 85))
        wav = self._sine_wav(duration_ms=duration_ms, freq_hz=freq_hz, sample_rate=sample_rate)
        return TtsResult(wav_bytes=wav, duration_ms=duration_ms, sample_rate=sample_rate)

    def _error_wav(self, message: str) -> TtsResult:
        wav = self._sine_wav(duration_ms=800, freq_hz=220.0, sample_rate=DEFAULT_SAMPLE_RATE)
        logger.error("%s", message)
        return TtsResult(wav_bytes=wav, duration_ms=800, sample_rate=DEFAULT_SAMPLE_RATE)

    @staticmethod
    def _sine_wav(duration_ms: int, freq_hz: float, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
        samples = int(sample_rate * (duration_ms / 1000.0))
        t = np.arange(samples, dtype=np.float64) / sample_rate
        audio = (0.35 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
        buf = io.BytesIO()
        pcm = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()
