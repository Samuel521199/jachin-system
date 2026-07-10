from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

try:
    from services.stt_hotwords import HotwordSnapshot, SttHotwordProvider
except ModuleNotFoundError:
    from voice_server.services.stt_hotwords import HotwordSnapshot, SttHotwordProvider

logger = logging.getLogger("jachin.voice_server.cloud_stt")
_MEANINGFUL_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


@dataclass
class CloudSttResult:
    text: str
    confidence: float
    duration_ms: int
    language: str
    hotword_count: int = 0
    hotword_status: str = "cloud_not_supported"
    hotword_sources: tuple[str, ...] = ()
    raw_text: str = ""
    user_message: str = ""
    user_message_source: str = ""
    reply_plan: dict[str, Any] = field(default_factory=dict)
    backend: str = "dashscope-qwen-asr"
    understanding: dict[str, Any] = field(default_factory=dict)


class CloudSttService:
    """DashScope cloud ASR service with the same interface as local STT."""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        ws_api_base: str = "",
        model: str = "fun-asr-realtime",
        realtime_model: str = "fun-asr-realtime",
        hotword_model: str = "fun-asr-realtime",
        file_model: str = "fun-asr",
        vocabulary_id: str = "",
        vocabulary_prefix: str = "jachin",
        auto_sync_vocabulary: bool = True,
        workspace: str = "",
        language: str = "",
    ) -> None:
        self.api_key = api_key.strip()
        self.api_base = api_base.rstrip("/")
        self.ws_api_base = ws_api_base.rstrip("/")
        self.model_name = model.strip() or "fun-asr-realtime"
        self.realtime_model = realtime_model.strip() or "fun-asr-realtime"
        self.hotword_model = hotword_model.strip() or "fun-asr-realtime"
        self.file_model = file_model.strip() or "fun-asr"
        self.vocabulary_id = vocabulary_id.strip()
        self.vocabulary_prefix = self._clean_vocabulary_prefix(vocabulary_prefix)
        self.auto_sync_vocabulary = bool(auto_sync_vocabulary)
        self.workspace = workspace.strip()
        self.language = language.strip()
        self.model_path = f"cloud:{self.model_name}"
        self._load_error: str | None = None
        self._sync_attempted = False
        self._synced_vocabulary_id = ""
        self._synced_vocabulary_signature = ""
        self._hotwords = SttHotwordProvider()
        self._client = requests.Session()

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.api_base)

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _load_engine(self) -> bool:
        if not self.ready:
            self._load_error = "DASHSCOPE_API_KEY is missing" if not self.api_key else "DashScope API base is missing"
            return False
        return True

    def transcribe(self, audio_bytes: bytes) -> CloudSttResult:
        duration_ms = self._estimate_duration_ms(audio_bytes)
        if not self.ready:
            return CloudSttResult(
                text="",
                confidence=0.0,
                duration_ms=duration_ms,
                language=self.language or "zh",
                backend=f"dashscope:{self.model_name}",
            )

        if self._uses_fun_asr_sdk(self.model_name):
            return self._transcribe_fun_asr(audio_bytes, duration_ms)

        return self._transcribe_qwen_compatible(audio_bytes, duration_ms)

    def _transcribe_qwen_compatible(self, audio_bytes: bytes, duration_ms: int) -> CloudSttResult:
        mime = self._guess_mime(audio_bytes)
        data_uri = "data:{};base64,{}".format(mime, base64.b64encode(audio_bytes).decode("ascii"))
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        }
                    ],
                }
            ],
            "stream": False,
            "asr_options": {"enable_itn": False},
        }
        if self.language:
            body["asr_options"]["language"] = self.language

        started = time.perf_counter()
        try:
            resp = self._client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=float(self._timeout_seconds()),
            )
            if not resp.ok:
                self._load_error = f"DashScope ASR HTTP {resp.status_code}: {resp.text[:500]}"
                logger.warning("DashScope ASR failed status=%s body=%s", resp.status_code, resp.text[:500])
                return self._error_result(self._load_error, duration_ms)
            payload = resp.json()
            raw_text = self._extract_text(payload)
            corrected = self._apply_domain_terms(self._sanitize_transcript_text(raw_text))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("DashScope ASR ok model=%s elapsed_ms=%s text_len=%s", self.model_name, elapsed_ms, len(corrected))
            return CloudSttResult(
                text=corrected,
                raw_text=raw_text,
                user_message="",
                user_message_source="",
                reply_plan={},
                confidence=0.92 if corrected else 0.0,
                duration_ms=duration_ms,
                language=self.language or "auto",
                backend=f"dashscope:{self.model_name}",
                understanding={},
            )
        except Exception as e:
            self._load_error = str(e)
            logger.exception("DashScope ASR exception")
            return self._error_result(f"DashScope ASR error: {e}", duration_ms)

    def _transcribe_fun_asr(self, audio_bytes: bytes, duration_ms: int) -> CloudSttResult:
        snapshot = self._hotwords.snapshot()
        model = self.hotword_model or self.realtime_model or self.model_name
        audio_format = self._audio_format_from_mime(self._guess_mime(audio_bytes))
        sample_rate = self._wav_sample_rate(audio_bytes) or 16000
        suffix = f".{audio_format}" if audio_format in {"wav", "mp3", "ogg", "flac"} else ".wav"
        temp_path = self._write_temp_audio(audio_bytes, suffix)
        started = time.perf_counter()
        try:
            self._configure_dashscope_sdk()
            Recognition = self._recognition_class()
            RecognitionCallback = self._recognition_callback_class()
            vocabulary_id = self._ensure_fun_asr_vocabulary(snapshot, model)

            kwargs: dict[str, Any] = {}
            if self.workspace:
                kwargs["workspace"] = self.workspace
            if vocabulary_id:
                kwargs["vocabulary_id"] = vocabulary_id
            if self.language:
                kwargs["language_hints"] = [self.language]

            recognition = Recognition(
                model=model,
                callback=RecognitionCallback(),
                format=audio_format,
                sample_rate=sample_rate,
                **kwargs,
            )

            call_kwargs: dict[str, Any] = {}
            raw_input = self._build_fun_asr_raw_input(snapshot)
            if raw_input:
                call_kwargs["raw_input"] = raw_input
            # DashScope older SDKs call this "phrase_id"; current docs call it
            # "vocabulary_id". Passing both places keeps native hotwords wired
            # across SDK/API naming changes.
            if vocabulary_id:
                call_kwargs["vocabulary_id"] = vocabulary_id

            result = recognition.call(
                str(temp_path),
                phrase_id=vocabulary_id or None,
                **call_kwargs,
            )
            if getattr(result, "status_code", 200) not in (200, "200", None):
                message = getattr(result, "message", "") or getattr(result, "code", "") or str(result)
                self._load_error = f"DashScope Fun-ASR {getattr(result, 'status_code', '')}: {message}"
                logger.warning("DashScope Fun-ASR failed: %s", self._load_error)
                return self._error_result(self._load_error, duration_ms)

            raw_text = self._extract_fun_asr_text(result)
            corrected = self._apply_domain_terms(self._sanitize_transcript_text(raw_text))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "DashScope Fun-ASR ok model=%s elapsed_ms=%s text_len=%s hotwords=%s vocab=%s",
                model,
                elapsed_ms,
                len(corrected),
                snapshot.count,
                "set" if self.vocabulary_id else "unset",
            )
            return CloudSttResult(
                text=corrected,
                raw_text=raw_text,
                user_message="",
                user_message_source="",
                reply_plan={},
                confidence=0.92 if corrected else 0.0,
                duration_ms=duration_ms,
                language=self.language or "auto",
                hotword_count=snapshot.count,
                hotword_status=self._fun_asr_hotword_status(snapshot, vocabulary_id),
                hotword_sources=tuple(self._fun_asr_hotword_sources(snapshot, vocabulary_id)),
                backend=f"dashscope:{model}",
                understanding={},
            )
        except Exception as e:
            self._load_error = str(e)
            logger.exception("DashScope Fun-ASR exception")
            return self._error_result(f"DashScope Fun-ASR error: {e}", duration_ms)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _error_result(self, message: str, duration_ms: int) -> CloudSttResult:
        return CloudSttResult(
            text=f"[STT error] {message}",
            confidence=0.0,
            duration_ms=duration_ms,
            language=self.language or "zh",
            backend=f"dashscope:{self.model_name}",
        )

    @staticmethod
    def _uses_fun_asr_sdk(model: str) -> bool:
        normalized = (model or "").strip().lower()
        return normalized.startswith("fun-asr") or normalized.startswith("paraformer")

    def _configure_dashscope_sdk(self) -> None:
        import dashscope  # type: ignore

        dashscope.api_key = self.api_key
        if self.api_base:
            dashscope.base_http_api_url = self._compatible_to_http_base(self.api_base)
        if self.ws_api_base:
            dashscope.base_websocket_api_url = self.ws_api_base
        elif self.api_base:
            dashscope.base_websocket_api_url = self._compatible_to_ws_base(self.api_base)

    @staticmethod
    def _compatible_to_http_base(api_base: str) -> str:
        base = api_base.rstrip("/")
        if base.endswith("/compatible-mode/v1"):
            return base[: -len("/compatible-mode/v1")] + "/api/v1"
        return base

    @staticmethod
    def _compatible_to_ws_base(api_base: str) -> str:
        base = api_base.rstrip("/")
        if base.endswith("/compatible-mode/v1"):
            base = base[: -len("/compatible-mode/v1")] + "/api-ws/v1/inference"
        elif not base.endswith("/api-ws/v1/inference"):
            base = base.rstrip("/") + "/api-ws/v1/inference"
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :]
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :]
        return base

    @staticmethod
    def _recognition_class() -> Any:
        from dashscope.audio.asr import Recognition  # type: ignore

        return Recognition

    @staticmethod
    def _recognition_callback_class() -> Any:
        from dashscope.audio.asr import RecognitionCallback  # type: ignore

        return RecognitionCallback

    @staticmethod
    def _vocabulary_service_class() -> Any:
        from dashscope.audio.asr import VocabularyService  # type: ignore

        return VocabularyService

    def _ensure_fun_asr_vocabulary(self, snapshot: HotwordSnapshot, target_model: str) -> str:
        current_id = self.vocabulary_id or self._synced_vocabulary_id
        signature = self._snapshot_signature(snapshot)
        if current_id and (not self.auto_sync_vocabulary or not snapshot.words):
            return current_id
        if current_id and self._synced_vocabulary_signature == signature:
            return current_id
        if self._sync_attempted and not current_id:
            return ""
        if not self.auto_sync_vocabulary or not snapshot.words:
            return current_id

        vocabulary = self._build_dashscope_vocabulary(snapshot)
        if not vocabulary:
            self._sync_attempted = True
            return current_id

        self._sync_attempted = True
        try:
            VocabularyService = self._vocabulary_service_class()
            service = VocabularyService(api_key=self.api_key, workspace=self.workspace or None)
            vocabulary_id = current_id or self._find_existing_vocabulary_id(service, self.vocabulary_prefix, target_model)
            if vocabulary_id:
                service.update_vocabulary(vocabulary_id, vocabulary)
                logger.info(
                    "Updated DashScope ASR vocabulary id=%s prefix=%s words=%s target_model=%s",
                    vocabulary_id,
                    self.vocabulary_prefix,
                    len(vocabulary),
                    target_model,
                )
            else:
                vocabulary_id = service.create_vocabulary(
                    target_model=target_model,
                    prefix=self.vocabulary_prefix,
                    vocabulary=vocabulary,
                )
                logger.info(
                    "Created DashScope ASR vocabulary id=%s prefix=%s words=%s target_model=%s",
                    vocabulary_id,
                    self.vocabulary_prefix,
                    len(vocabulary),
                    target_model,
                )
            self._synced_vocabulary_id = str(vocabulary_id or "").strip()
            self._synced_vocabulary_signature = signature
            return self._synced_vocabulary_id
        except Exception as e:
            logger.warning("DashScope ASR vocabulary sync skipped: %s", e)
            return current_id

    @staticmethod
    def _snapshot_signature(snapshot: HotwordSnapshot) -> str:
        items = sorted((str(k), int(v)) for k, v in snapshot.words.items())
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _find_existing_vocabulary_id(service: Any, prefix: str, target_model: str) -> str:
        items = service.list_vocabularies(prefix=prefix, page_index=0, page_size=10) or []
        normalized_target = (target_model or "").strip().lower()
        for item in items:
            if not isinstance(item, dict):
                continue
            item_model = str(item.get("target_model") or item.get("targetModel") or "").strip().lower()
            if item_model and item_model != normalized_target:
                continue
            vocabulary_id = item.get("vocabulary_id") or item.get("vocabularyId") or item.get("id")
            if vocabulary_id:
                return str(vocabulary_id)
        return ""

    @staticmethod
    def _build_dashscope_vocabulary(snapshot: HotwordSnapshot) -> list[dict[str, Any]]:
        vocabulary: list[dict[str, Any]] = []
        seen: set[str] = set()
        ordered = sorted(snapshot.words.items(), key=lambda item: (-item[1], item[0].lower()))
        for word, weight in ordered:
            text = str(word or "").strip()
            if not text or text.lower() in seen:
                continue
            if not CloudSttService._is_valid_dashscope_hotword(text):
                continue
            seen.add(text.lower())
            vocabulary.append({"text": text, "weight": CloudSttService._dashscope_hotword_weight(weight)})
            if len(vocabulary) >= 500:
                break
        return vocabulary

    @staticmethod
    def _is_valid_dashscope_hotword(text: str) -> bool:
        if not text or not _MEANINGFUL_CHAR_RE.search(text):
            return False
        if any(ord(ch) > 127 for ch in text):
            return len(text) <= 15
        return len(text.split()) <= 7

    @staticmethod
    def _dashscope_hotword_weight(weight: int) -> int:
        try:
            value = int(weight)
        except Exception:
            value = 20
        if value >= 90:
            return 5
        if value >= 60:
            return 4
        if value >= 30:
            return 3
        if value >= 10:
            return 2
        return 1

    @staticmethod
    def _clean_vocabulary_prefix(prefix: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]", "", str(prefix or "").strip().lower())
        return (cleaned or "jachin")[:9]

    @staticmethod
    def _write_temp_audio(audio_bytes: bytes, suffix: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(prefix="jachin-fun-asr-", suffix=suffix, delete=False)
        try:
            tmp.write(audio_bytes)
            return Path(tmp.name)
        finally:
            tmp.close()

    @staticmethod
    def _audio_format_from_mime(mime: str) -> str:
        if mime == "audio/mpeg":
            return "mp3"
        if mime == "audio/ogg":
            return "ogg"
        if mime == "audio/flac":
            return "flac"
        return "wav"

    @staticmethod
    def _wav_sample_rate(audio_bytes: bytes) -> int:
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                return int(wf.getframerate() or 16000)
        except Exception:
            return 16000

    @staticmethod
    def _build_fun_asr_raw_input(snapshot: HotwordSnapshot) -> dict[str, Any]:
        if not snapshot.words:
            return {}
        ordered = sorted(snapshot.words.items(), key=lambda item: (-item[1], item[0].lower()))
        parts: list[str] = []
        char_budget = 360
        for word, _weight in ordered:
            candidate = str(word).strip()
            if not candidate:
                continue
            next_text = "、".join([*parts, candidate])
            if len(next_text) > char_budget:
                break
            parts.append(candidate)
        if not parts:
            return {}
        text = "优先准确识别这些业务词和人名：" + "、".join(parts)
        return {
            "context": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": text[:400]}],
                }
            ]
        }

    def _fun_asr_hotword_status(self, snapshot: HotwordSnapshot, vocabulary_id: str) -> str:
        if vocabulary_id and snapshot.words:
            return "native_vocabulary_id+context_terms"
        if vocabulary_id:
            return "native_vocabulary_id"
        if snapshot.words:
            return "context_terms_only"
        return "not_configured"

    def _fun_asr_hotword_sources(self, snapshot: HotwordSnapshot, vocabulary_id: str) -> list[str]:
        sources = list(snapshot.sources)
        if vocabulary_id:
            sources.append("dashscope:vocabulary_id")
        if snapshot.words:
            sources.append("dashscope:raw_input.context")
        return sources

    @staticmethod
    def _extract_fun_asr_text(result: Any) -> str:
        sentence: Any = None
        try:
            sentence = result.get_sentence()
        except Exception:
            sentence = None
        if sentence is None and isinstance(result, dict):
            sentence = (result.get("output") or {}).get("sentence")
        if sentence is None:
            sentence = getattr(result, "output", {}).get("sentence") if isinstance(getattr(result, "output", None), dict) else None
        if isinstance(sentence, list):
            parts = []
            for item in sentence:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(str(item or ""))
            return "".join(parts).strip()
        if isinstance(sentence, dict):
            return str(sentence.get("text") or "").strip()
        return str(sentence or "").strip()

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        try:
            choices = payload.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            parts.append(str(item.get("text") or item.get("content") or ""))
                    return "".join(parts).strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _sanitize_transcript_text(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned or not _MEANINGFUL_CHAR_RE.search(cleaned):
            return ""
        return cleaned

    @staticmethod
    def _guess_mime(audio_bytes: bytes) -> str:
        head = audio_bytes[:16]
        if head.startswith(b"RIFF") and b"WAVE" in head:
            return "audio/wav"
        if head.startswith(b"ID3") or head[:2] == b"\xff\xfb":
            return "audio/mpeg"
        if head.startswith(b"OggS"):
            return "audio/ogg"
        if head.startswith(b"fLaC"):
            return "audio/flac"
        return "audio/wav"

    @staticmethod
    def _estimate_duration_ms(audio_bytes: bytes) -> int:
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 16000
                duration = int((frames / rate) * 1000)
                if 0 < duration < 60 * 60 * 1000:
                    return duration
        except Exception:
            pass
        try:
            if audio_bytes.startswith(b"RIFF") and len(audio_bytes) > 44:
                channels = int.from_bytes(audio_bytes[22:24], "little") or 1
                sample_rate = int.from_bytes(audio_bytes[24:28], "little") or 16000
                bits_per_sample = int.from_bytes(audio_bytes[34:36], "little") or 16
                bytes_per_second = sample_rate * channels * max(1, bits_per_sample // 8)
                if bytes_per_second > 0:
                    return int(max(0, len(audio_bytes) - 44) * 1000 / bytes_per_second)
        except Exception:
            pass
        return 0

    @staticmethod
    def _apply_domain_terms(text: str) -> str:
        if not text:
            return ""
        raw = os.getenv(
            "JACHIN_STT_DOMAIN_TERMS",
            "Jochen=Jachin,Jachi=Jachin,Jaqin=Jachin,Cortex=Codex,Lock=Lark",
        )
        out = text
        for item in raw.split(","):
            if "=" not in item:
                continue
            src, dst = item.split("=", 1)
            src = src.strip()
            dst = dst.strip()
            if src and dst:
                out = re.sub(rf"\b{re.escape(src)}\b", dst, out, flags=re.IGNORECASE)
        return out

    @staticmethod
    def _choose_transcript(raw_text: str, corrected_text: str) -> str:
        raw = (raw_text or "").strip()
        corrected = (corrected_text or "").strip()
        if not corrected:
            return raw
        raw_meaningful = re.sub(r"\s+", "", raw)
        corrected_meaningful = re.sub(r"\s+", "", corrected)
        if len(raw_meaningful) >= 8 and len(corrected_meaningful) < max(4, int(len(raw_meaningful) * 0.55)):
            return raw
        return corrected

    @staticmethod
    def _timeout_seconds() -> float:
        try:
            import os

            return max(3.0, min(60.0, float(os.getenv("JACHIN_STT_TIMEOUT_SEC", "20"))))
        except Exception:
            return 20.0
