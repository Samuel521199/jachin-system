from __future__ import annotations

import base64
import io
import json
import logging
import os
import queue
import re
import socket
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


def _diag_event(events: list[dict[str, Any]], stage: str, started: float, **payload: Any) -> None:
    event = {
        "stage": stage,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
    event.update({k: v for k, v in payload.items() if v is not None})
    events.append(event)


def _attach_cloud_diagnostics(result: CloudSttResult, events: list[dict[str, Any]]) -> CloudSttResult:
    result.understanding = {
        **(result.understanding or {}),
        "cloud_diagnostics": list(events),
    }
    return result


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
        diag_started = time.perf_counter()
        diag_events: list[dict[str, Any]] = []
        _diag_event(
            diag_events,
            "cloud_start",
            diag_started,
            model=self.model_name,
            bytes=len(audio_bytes),
            duration_ms=duration_ms,
            api_base=self.api_base,
            ws_api_base=self.ws_api_base,
        )
        if not self.ready:
            _diag_event(
                diag_events,
                "cloud_exception",
                diag_started,
                reason="not_ready",
                api_key_set=bool(self.api_key),
                api_base_set=bool(self.api_base),
            )
            return _attach_cloud_diagnostics(CloudSttResult(
                text="",
                confidence=0.0,
                duration_ms=duration_ms,
                language=self.language or "zh",
                backend=f"dashscope:{self.model_name}",
            ), diag_events)

        if self._uses_fun_asr_sdk(self.model_name):
            return self._transcribe_fun_asr(audio_bytes, duration_ms, diag_events, diag_started)

        return self._transcribe_qwen_compatible(audio_bytes, duration_ms, diag_events, diag_started)

    def _transcribe_qwen_compatible(
        self,
        audio_bytes: bytes,
        duration_ms: int,
        diag_events: list[dict[str, Any]],
        diag_started: float,
    ) -> CloudSttResult:
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
            self._probe_network(self.api_base, diag_events, diag_started, label="cloud_http")
            _diag_event(diag_events, "cloud_upload_start", diag_started, mode="http_compatible", mime=mime)
            resp = self._client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=float(self._timeout_seconds()),
            )
            _diag_event(
                diag_events,
                "cloud_response_headers",
                diag_started,
                status_code=resp.status_code,
                response_ms=int((time.perf_counter() - started) * 1000),
            )
            if not resp.ok:
                self._load_error = f"DashScope ASR HTTP {resp.status_code}: {resp.text[:500]}"
                logger.warning("DashScope ASR failed status=%s body=%s", resp.status_code, resp.text[:500])
                _diag_event(diag_events, "cloud_exception", diag_started, status_code=resp.status_code)
                return _attach_cloud_diagnostics(self._error_result(self._load_error, duration_ms), diag_events)
            payload = resp.json()
            raw_text = self._extract_text(payload)
            corrected = self._sanitize_transcript_text(raw_text)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("DashScope ASR ok model=%s elapsed_ms=%s text_len=%s", self.model_name, elapsed_ms, len(corrected))
            _diag_event(diag_events, "cloud_result", diag_started, elapsed_ms=elapsed_ms, text_len=len(corrected))
            return _attach_cloud_diagnostics(CloudSttResult(
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
            ), diag_events)
        except Exception as e:
            self._load_error = str(e)
            _diag_event(diag_events, "cloud_exception", diag_started, error=repr(e))
            logger.exception("DashScope ASR exception")
            return _attach_cloud_diagnostics(self._error_result(f"DashScope ASR error: {e}", duration_ms), diag_events)

    def _transcribe_fun_asr(
        self,
        audio_bytes: bytes,
        duration_ms: int,
        diag_events: list[dict[str, Any]],
        diag_started: float,
    ) -> CloudSttResult:
        _diag_event(diag_events, "cloud_hotwords_snapshot_start", diag_started)
        snapshot = self._hotwords.snapshot()
        _diag_event(
            diag_events,
            "cloud_hotwords_snapshot_done",
            diag_started,
            hotword_count=snapshot.count,
            source_count=len(snapshot.sources),
        )
        model = self.hotword_model or self.realtime_model or self.model_name
        audio_format = self._audio_format_from_mime(self._guess_mime(audio_bytes))
        sample_rate = self._wav_sample_rate(audio_bytes) or 16000
        suffix = f".{audio_format}" if audio_format in {"wav", "mp3", "ogg", "flac"} else ".wav"
        temp_path = self._write_temp_audio(audio_bytes, suffix)
        _diag_event(
            diag_events,
            "cloud_temp_audio_written",
            diag_started,
            path=str(temp_path),
            bytes=len(audio_bytes),
            audio_format=audio_format,
            sample_rate=sample_rate,
        )
        started = time.perf_counter()
        try:
            self._probe_network(self.ws_api_base or self.api_base, diag_events, diag_started, label="cloud_ws")
            _diag_event(diag_events, "cloud_sdk_config_start", diag_started)
            self._configure_dashscope_sdk()
            _diag_event(diag_events, "cloud_sdk_config_done", diag_started)
            Recognition = self._recognition_class()
            RecognitionCallback = self._recognition_callback_class()
            vocabulary_id = self._ensure_fun_asr_vocabulary(snapshot, model, diag_events, diag_started)

            kwargs: dict[str, Any] = {}
            if self.workspace:
                kwargs["workspace"] = self.workspace
            if vocabulary_id:
                kwargs["vocabulary_id"] = vocabulary_id
            if self.language:
                kwargs["language_hints"] = [self.language]

            _diag_event(
                diag_events,
                "cloud_recognition_init",
                diag_started,
                model=model,
                audio_format=audio_format,
                sample_rate=sample_rate,
                vocabulary_id_set=bool(vocabulary_id),
            )
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

            _diag_event(
                diag_events,
                "cloud_upload_start",
                diag_started,
                mode="dashscope_recognition_call",
                note="sdk_call_includes_upload_and_server_queue",
            )
            result = recognition.call(
                str(temp_path),
                phrase_id=vocabulary_id or None,
                **call_kwargs,
            )
            _diag_event(
                diag_events,
                "cloud_sdk_call_done",
                diag_started,
                request_id=getattr(result, "request_id", None),
                status_code=getattr(result, "status_code", None),
            )
            if getattr(result, "status_code", 200) not in (200, "200", None):
                message = getattr(result, "message", "") or getattr(result, "code", "") or str(result)
                self._load_error = f"DashScope Fun-ASR {getattr(result, 'status_code', '')}: {message}"
                logger.warning("DashScope Fun-ASR failed: %s", self._load_error)
                _diag_event(diag_events, "cloud_exception", diag_started, status_code=getattr(result, "status_code", None), message=message)
                return _attach_cloud_diagnostics(self._error_result(self._load_error, duration_ms), diag_events)

            raw_text = self._extract_fun_asr_text(result)
            corrected = self._sanitize_transcript_text(raw_text)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            _diag_event(
                diag_events,
                "cloud_result",
                diag_started,
                elapsed_ms=elapsed_ms,
                text_len=len(corrected),
                first_package_delay_ms=self._safe_call(getattr(recognition, "get_first_package_delay", None)),
                last_package_delay_ms=self._safe_call(getattr(recognition, "get_last_package_delay", None)),
                request_id=self._safe_call(getattr(recognition, "get_last_request_id", None)),
            )
            logger.info(
                "DashScope Fun-ASR ok model=%s elapsed_ms=%s text_len=%s hotwords=%s vocab=%s",
                model,
                elapsed_ms,
                len(corrected),
                snapshot.count,
                "set" if self.vocabulary_id else "unset",
            )
            return _attach_cloud_diagnostics(CloudSttResult(
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
            ), diag_events)
        except Exception as e:
            self._load_error = str(e)
            _diag_event(diag_events, "cloud_exception", diag_started, error=repr(e))
            logger.exception("DashScope Fun-ASR exception")
            return _attach_cloud_diagnostics(self._error_result(f"DashScope Fun-ASR error: {e}", duration_ms), diag_events)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
                _diag_event(diag_events, "cloud_temp_audio_removed", diag_started)
            except Exception:
                pass

    def start_stream_session(self, sample_rate: int = 16000, session_id: str | None = None) -> "CloudSttStreamSession":
        """Start a real DashScope Recognition stream session.

        JVS exposes both WebSocket and raw TCP streaming endpoints. This method
        is the cloud-backed implementation behind those endpoints: audio frames
        are pushed to DashScope as they are captured instead of waiting for a
        complete WAV file.
        """
        if not self.ready:
            raise RuntimeError("DashScope STT is not ready")
        diag_started = time.perf_counter()
        diag_events: list[dict[str, Any]] = []
        model = self.hotword_model or self.realtime_model or self.model_name
        _diag_event(
            diag_events,
            "cloud_stream_start",
            diag_started,
            model=model,
            sample_rate=sample_rate,
            session_id=session_id,
            api_base=self.api_base,
            ws_api_base=self.ws_api_base,
        )
        snapshot = self._hotwords.snapshot()
        self._probe_network(self.ws_api_base or self.api_base, diag_events, diag_started, label="cloud_stream_ws")
        self._configure_dashscope_sdk()
        Recognition = self._recognition_class()
        RecognitionCallback = self._recognition_callback_class()
        vocabulary_id = self._ensure_fun_asr_vocabulary(snapshot, model, diag_events, diag_started)

        class _Callback(RecognitionCallback):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                self.events: "queue.Queue[dict[str, Any]]" = queue.Queue()
                self.latest_text = ""
                self.final_texts: list[str] = []
                self.error: str | None = None
                self.completed = threading.Event()
                self.opened = threading.Event()

            def on_open(self) -> None:
                _diag_event(diag_events, "cloud_stream_open", diag_started)
                self.opened.set()
                self.events.put({"type": "open"})

            def on_event(self, result: Any) -> None:
                sentence = None
                try:
                    sentence = result.get_sentence()
                except Exception:
                    sentence = None
                text = CloudSttService._extract_fun_asr_text(result)
                if not text:
                    return
                is_final = False
                if isinstance(sentence, dict):
                    is_final = bool(sentence.get("end_time") is not None)
                elif isinstance(sentence, list):
                    is_final = any(isinstance(item, dict) and item.get("end_time") is not None for item in sentence)
                corrected = CloudSttService._sanitize_transcript_text(text)
                if not corrected:
                    return
                self.latest_text = corrected
                if is_final:
                    self.final_texts.append(corrected)
                _diag_event(
                    diag_events,
                    "cloud_stream_event",
                    diag_started,
                    event_type="final" if is_final else "partial",
                    text_len=len(corrected),
                    request_id=getattr(result, "request_id", None),
                )
                self.events.put(
                    {
                        "type": "final" if is_final else "partial",
                        "text": corrected,
                        "raw_text": text,
                        "request_id": getattr(result, "request_id", None),
                    }
                )

            def on_complete(self) -> None:
                _diag_event(diag_events, "cloud_stream_complete", diag_started)
                self.completed.set()
                self.events.put({"type": "complete"})

            def on_error(self, result: Any) -> None:
                message = getattr(result, "message", "") or getattr(result, "code", "") or str(result)
                self.error = str(message)
                _diag_event(diag_events, "cloud_stream_error", diag_started, message=self.error)
                self.completed.set()
                self.events.put({"type": "error", "message": self.error})

            def on_close(self) -> None:
                _diag_event(diag_events, "cloud_stream_close", diag_started)
                self.events.put({"type": "close"})

        callback = _Callback()
        kwargs: dict[str, Any] = {}
        if self.workspace:
            kwargs["workspace"] = self.workspace
        if vocabulary_id:
            kwargs["vocabulary_id"] = vocabulary_id
        if self.language:
            kwargs["language_hints"] = [self.language]
        recognition = Recognition(
            model=model,
            callback=callback,
            format="pcm",
            sample_rate=int(sample_rate or 16000),
            **kwargs,
        )
        call_kwargs: dict[str, Any] = {}
        raw_input = self._build_fun_asr_raw_input(snapshot)
        if raw_input:
            call_kwargs["raw_input"] = raw_input
        if vocabulary_id:
            call_kwargs["vocabulary_id"] = vocabulary_id
        recognition.start(phrase_id=vocabulary_id or None, **call_kwargs)
        _diag_event(
            diag_events,
            "cloud_stream_started",
            diag_started,
            hotword_count=snapshot.count,
            vocabulary_id_set=bool(vocabulary_id),
        )
        return CloudSttStreamSession(
            service=self,
            recognition=recognition,
            callback=callback,
            model=model,
            language=self.language or "auto",
            sample_rate=int(sample_rate or 16000),
            hotword_snapshot=snapshot,
            vocabulary_id=vocabulary_id,
            diag_events=diag_events,
            diag_started=diag_started,
        )

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

    def _ensure_fun_asr_vocabulary(
        self,
        snapshot: HotwordSnapshot,
        target_model: str,
        diag_events: list[dict[str, Any]] | None = None,
        diag_started: float | None = None,
    ) -> str:
        current_id = self.vocabulary_id or self._synced_vocabulary_id
        signature = self._snapshot_signature(snapshot)
        if current_id and (not self.auto_sync_vocabulary or not snapshot.words):
            if diag_events is not None and diag_started is not None:
                _diag_event(
                    diag_events,
                    "cloud_vocabulary_sync_skipped",
                    diag_started,
                    reason="configured_or_empty",
                    vocabulary_id_set=bool(current_id),
                    hotword_count=snapshot.count,
                )
            return current_id
        if current_id and self._synced_vocabulary_signature == signature:
            if diag_events is not None and diag_started is not None:
                _diag_event(
                    diag_events,
                    "cloud_vocabulary_sync_skipped",
                    diag_started,
                    reason="signature_unchanged",
                    vocabulary_id_set=True,
                    hotword_count=snapshot.count,
                )
            return current_id
        if self._sync_attempted and not current_id:
            if diag_events is not None and diag_started is not None:
                _diag_event(diag_events, "cloud_vocabulary_sync_skipped", diag_started, reason="already_attempted_without_id")
            return ""
        if not self.auto_sync_vocabulary or not snapshot.words:
            if diag_events is not None and diag_started is not None:
                _diag_event(
                    diag_events,
                    "cloud_vocabulary_sync_skipped",
                    diag_started,
                    reason="disabled_or_no_hotwords",
                    auto_sync=self.auto_sync_vocabulary,
                    hotword_count=snapshot.count,
                )
            return current_id

        vocabulary = self._build_dashscope_vocabulary(snapshot)
        if not vocabulary:
            self._sync_attempted = True
            if diag_events is not None and diag_started is not None:
                _diag_event(diag_events, "cloud_vocabulary_sync_skipped", diag_started, reason="empty_valid_vocabulary")
            return current_id

        self._sync_attempted = True
        try:
            if diag_events is not None and diag_started is not None:
                _diag_event(
                    diag_events,
                    "cloud_vocabulary_sync_start",
                    diag_started,
                    words=len(vocabulary),
                    target_model=target_model,
                    existing_id_set=bool(current_id),
                )
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
                if diag_events is not None and diag_started is not None:
                    _diag_event(
                        diag_events,
                        "cloud_vocabulary_sync_done",
                        diag_started,
                        action="update",
                        vocabulary_id_set=True,
                        words=len(vocabulary),
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
                if diag_events is not None and diag_started is not None:
                    _diag_event(
                        diag_events,
                        "cloud_vocabulary_sync_done",
                        diag_started,
                        action="create",
                        vocabulary_id_set=bool(vocabulary_id),
                        words=len(vocabulary),
                    )
            self._synced_vocabulary_id = str(vocabulary_id or "").strip()
            self._synced_vocabulary_signature = signature
            return self._synced_vocabulary_id
        except Exception as e:
            logger.warning("DashScope ASR vocabulary sync skipped: %s", e)
            if diag_events is not None and diag_started is not None:
                _diag_event(diag_events, "cloud_vocabulary_sync_exception", diag_started, error=repr(e))
            return current_id

    @staticmethod
    def _safe_call(fn: Any) -> Any:
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:
            return None

    @staticmethod
    def _probe_network(
        url: str,
        diag_events: list[dict[str, Any]],
        diag_started: float,
        *,
        label: str,
    ) -> None:
        if os.getenv("JACHIN_STT_CLOUD_NETWORK_PROBE", "1").strip().lower() in {"0", "false", "no", "off"}:
            _diag_event(diag_events, "cloud_network_probe_skipped", diag_started, label=label, reason="disabled")
            return
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.hostname or ""
        if not host:
            _diag_event(diag_events, "cloud_dns", diag_started, label=label, ok=False, reason="missing_host")
            return
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
        try:
            dns_started = time.perf_counter()
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            _diag_event(
                diag_events,
                "cloud_dns",
                diag_started,
                label=label,
                ok=True,
                host=host,
                port=port,
                addr_count=len(infos),
                latency_ms=int((time.perf_counter() - dns_started) * 1000),
            )
        except Exception as e:
            _diag_event(diag_events, "cloud_dns", diag_started, label=label, ok=False, host=host, port=port, error=repr(e))
            return
        try:
            connect_started = time.perf_counter()
            with socket.create_connection((host, port), timeout=1.5):
                pass
            _diag_event(
                diag_events,
                "cloud_connect",
                diag_started,
                label=label,
                ok=True,
                host=host,
                port=port,
                latency_ms=int((time.perf_counter() - connect_started) * 1000),
            )
        except Exception as e:
            _diag_event(diag_events, "cloud_connect", diag_started, label=label, ok=False, host=host, port=port, error=repr(e))

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
            next_text = ", ".join([*parts, candidate])
            if len(next_text) > char_budget:
                break
            parts.append(candidate)
        if not parts:
            return {}
        text = "Prioritize recognizing these domain terms and names: " + ", ".join(parts)
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
    def _timeout_seconds() -> float:
        try:
            import os

            return max(3.0, min(60.0, float(os.getenv("JACHIN_STT_TIMEOUT_SEC", "20"))))
        except Exception:
            return 20.0


class CloudSttStreamSession:
    def __init__(
        self,
        *,
        service: CloudSttService,
        recognition: Any,
        callback: Any,
        model: str,
        language: str,
        sample_rate: int,
        hotword_snapshot: HotwordSnapshot,
        vocabulary_id: str,
        diag_events: list[dict[str, Any]],
        diag_started: float,
    ) -> None:
        self.service = service
        self.recognition = recognition
        self.callback = callback
        self.model = model
        self.language = language
        self.sample_rate = int(sample_rate or 16000)
        self.hotword_snapshot = hotword_snapshot
        self.vocabulary_id = vocabulary_id
        self.diag_events = diag_events
        self.diag_started = diag_started
        self.bytes_sent = 0
        self.closed = False

    def push_pcm(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes or self.closed:
            return
        self.bytes_sent += len(pcm_bytes)
        self.recognition.send_audio_frame(pcm_bytes)
        if self.bytes_sent == len(pcm_bytes) or self.bytes_sent % 32000 < len(pcm_bytes):
            _diag_event(
                self.diag_events,
                "cloud_stream_audio_sent",
                self.diag_started,
                bytes_sent=self.bytes_sent,
            )

    def poll_events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while True:
            try:
                out.append(self.callback.events.get_nowait())
            except queue.Empty:
                break
        return out

    def finish(self, timeout_sec: float = 3.0) -> CloudSttResult:
        if not self.closed:
            _diag_event(
                self.diag_events,
                "cloud_stream_stop_start",
                self.diag_started,
                bytes_sent=self.bytes_sent,
                timeout_sec=timeout_sec,
            )
            stop_done = threading.Event()

            def _stop_recognition() -> None:
                try:
                    self.recognition.stop()
                finally:
                    stop_done.set()

            threading.Thread(
                target=_stop_recognition,
                name="dashscope-stt-stream-stop",
                daemon=True,
            ).start()
            stop_done.wait(timeout=max(0.1, float(timeout_sec)))
            if not stop_done.is_set():
                _diag_event(
                    self.diag_events,
                    "cloud_stream_stop_timeout",
                    self.diag_started,
                    timeout_sec=timeout_sec,
                )
            self.closed = True
        completed = self.callback.completed.wait(timeout=0.05)
        self.poll_events()
        has_final_text = bool("".join(self.callback.final_texts).strip())
        final_text = "".join(self.callback.final_texts).strip() or str(self.callback.latest_text or "").strip()
        used_latest_partial = bool(final_text and not has_final_text)
        duration_ms = int((self.bytes_sent / max(1, self.sample_rate * 2)) * 1000)
        if self.callback.error and not final_text:
            _diag_event(
                self.diag_events,
                "cloud_stream_final_error",
                self.diag_started,
                error=self.callback.error,
            )
            return _attach_cloud_diagnostics(
                self.service._error_result(f"DashScope stream error: {self.callback.error}", duration_ms),
                self.diag_events,
            )
        if not completed or not has_final_text:
            _diag_event(
                self.diag_events,
                "cloud_stream_final_timeout",
                self.diag_started,
                completed=completed,
                has_final_text=has_final_text,
                used_latest_partial=used_latest_partial,
                timeout_sec=timeout_sec,
            )
        _diag_event(
            self.diag_events,
            "cloud_stream_final",
            self.diag_started,
            text_len=len(final_text),
            bytes_sent=self.bytes_sent,
            duration_ms=duration_ms,
            first_package_delay_ms=CloudSttService._safe_call(getattr(self.recognition, "get_first_package_delay", None)),
            last_package_delay_ms=CloudSttService._safe_call(getattr(self.recognition, "get_last_package_delay", None)),
            request_id=CloudSttService._safe_call(getattr(self.recognition, "get_last_request_id", None)),
        )
        return _attach_cloud_diagnostics(
            CloudSttResult(
                text=final_text,
                raw_text=final_text,
                user_message="",
                user_message_source="",
                reply_plan={},
                confidence=0.92 if final_text else 0.0,
                duration_ms=duration_ms,
                language=self.language,
                hotword_count=self.hotword_snapshot.count,
                hotword_status=self.service._fun_asr_hotword_status(self.hotword_snapshot, self.vocabulary_id),
                hotword_sources=tuple(self.service._fun_asr_hotword_sources(self.hotword_snapshot, self.vocabulary_id)),
                backend=f"dashscope:{self.model}:stream",
                understanding={
                    "streaming_mode": "dashscope_recognition_start_send_audio_frame",
                    "stream_completed": completed,
                    "stream_finalized": has_final_text,
                    "stream_used_latest_partial": used_latest_partial,
                },
            ),
            self.diag_events,
        )


