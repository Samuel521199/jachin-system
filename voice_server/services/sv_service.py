from __future__ import annotations

import io
import logging
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

TARGET_SR = 16000
MIN_AUDIO_SAMPLES = int(0.25 * TARGET_SR)
DEFAULT_WIN_STEP_MS = 250
DEFAULT_WIN_LEN_MS = 900
DEFAULT_WINDOW_HIGH = 0.38
DEFAULT_WINDOW_LOW = 0.25
OWNER_EXIT_BELOW_LOW_COUNT = 2
OWNER_PRE_PAD_MS = 250
OWNER_POST_PAD_MS = 200
OWNER_MERGE_GAP_MS = 300
OWNER_JOIN_SILENCE_MS = 200
logger = logging.getLogger("jachin.voice_server.sv")


@dataclass
class VerifyResult:
    score: float
    is_match: bool
    reason: str


class SvService:
    """
    CAM++ SV 服务：
    - 对外提供 extract / verify / label_windows / filter_owner_track 所需基础能力
    - 统一使用 data/models/voice/sv 下的本地 CAM++ ModelScope 模型
    - 不再静默回退到谱统计 MVP，避免声纹过滤“看似可用但实际不准”
    """

    def __init__(self, sv_dir: Path, device: str = "auto", require_gpu: bool = False) -> None:
        self.sv_dir = sv_dir
        self.device_request = (device or "auto").strip().lower()
        self.require_gpu = bool(require_gpu)
        self.effective_device = "unresolved"
        self._engine: Any = None
        self._load_error: str | None = None
        self._backend: str = "cam++-modelscope-local"
        self._load_attempted = False
        self._engine_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._engine is not None

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _resolve_torch_device(self) -> str:
        request = self.device_request
        if request in {"gpu", "cuda"}:
            request = "cuda:0"
        if request in {"cpu"}:
            return "cpu"
        try:
            import torch

            cuda_ready = bool(torch.cuda.is_available())
            if request.startswith("cuda"):
                if cuda_ready:
                    return request
                if self.require_gpu:
                    raise RuntimeError(
                        f"GPU is required but PyTorch CUDA is unavailable "
                        f"(torch={getattr(torch, '__version__', 'unknown')}, cuda={getattr(torch.version, 'cuda', None)})"
                    )
                logger.warning(
                    "SV requested CUDA but PyTorch CUDA is unavailable; falling back to CPU "
                    "(torch=%s, cuda=%s)",
                    getattr(torch, "__version__", "unknown"),
                    getattr(torch.version, "cuda", None),
                )
                return "cpu"
            if cuda_ready:
                return "cuda:0"
            if self.require_gpu:
                raise RuntimeError(
                    f"GPU is required but PyTorch CUDA is unavailable "
                    f"(torch={getattr(torch, '__version__', 'unknown')}, cuda={getattr(torch.version, 'cuda', None)})"
                )
            return "cpu"
        except RuntimeError:
            raise
        except Exception as e:
            if self.require_gpu:
                raise RuntimeError(f"GPU is required but torch device detection failed: {e}") from e
            logger.warning("SV torch device detection failed; falling back to CPU: %s", e)
            return "cpu"

    def warmup(self) -> None:
        self._load_engine()

    def _has_local_camplus_files(self) -> bool:
        conf = self.sv_dir / "configuration.json"
        model_bin = self.sv_dir / "campplus_cn_common.bin"
        return conf.is_file() and model_bin.is_file() and model_bin.stat().st_size > 1024 * 1024

    def _load_engine(self) -> Any | None:
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            if self._load_attempted:
                return None
            self._load_attempted = True

            if not self._has_local_camplus_files():
                self._load_error = (
                    f"CAM++ weights missing or incomplete under {self.sv_dir} "
                    "(need configuration.json + campplus_cn_common.bin > 1MB)"
                )
                self._backend = "cam++-unavailable"
                logger.error("SV CAM++ not loaded: %s", self._load_error)
                return None

            try:
                from modelscope.pipelines import pipeline

                self.effective_device = self._resolve_torch_device()
                logger.info(
                    "Loading CAM++ speaker-verification pipeline from %s (device_request=%s, effective_device=%s)",
                    self.sv_dir,
                    self.device_request,
                    self.effective_device,
                )
                self._engine = pipeline(
                    task="speaker-verification",
                    model=str(self.sv_dir),
                    device=self.effective_device,
                )
                self._backend = "cam++-modelscope-local"
                self._load_error = None
                logger.info("SV CAM++ loaded successfully on %s", self.effective_device)
                return self._engine
            except Exception as e:
                self._load_error = f"{type(e).__name__}: {e}"
                self._backend = "cam++-unavailable"
                logger.exception("SV CAM++ load failed: %s", e)
                return None

    def extract_embedding(self, audio_bytes: bytes) -> np.ndarray:
        audio, sr = self._decode_audio_bytes(audio_bytes)
        if audio is None or len(audio) < MIN_AUDIO_SAMPLES:
            raise ValueError("audio too short or decode failed")
        wav16 = self._resample_to_16k(audio, sr)
        camppus_emb = self._extract_embedding_camplus(wav16)
        if camppus_emb is not None:
            return camppus_emb
        raise RuntimeError(self._sv_unavailable_message())

    def verify(self, audio_bytes: bytes, centroid: list[float], threshold: float) -> VerifyResult:
        if not centroid:
            return VerifyResult(score=0.0, is_match=False, reason="empty_centroid")
        emb = self.extract_embedding(audio_bytes)
        c = np.asarray(centroid, dtype=np.float32)
        if c.ndim != 1 or c.size == 0:
            return VerifyResult(score=0.0, is_match=False, reason="invalid_centroid")
        c = self._l2norm(c)
        score = float(np.clip(np.dot(self._l2norm(emb), c), -1.0, 1.0))
        return VerifyResult(
            score=score,
            is_match=score >= float(threshold),
            reason="ok",
        )

    def label_windows(
        self,
        audio_bytes: bytes,
        centroid: list[float],
        *,
        win_step_ms: int = DEFAULT_WIN_STEP_MS,
        win_len_ms: int = DEFAULT_WIN_LEN_MS,
        win_threshold_high: float = DEFAULT_WINDOW_HIGH,
        win_threshold_low: float = DEFAULT_WINDOW_LOW,
        debounce_count: int = 1,
    ) -> list[dict]:
        audio, sr = self._decode_audio_bytes(audio_bytes)
        if audio is None or len(audio) < MIN_AUDIO_SAMPLES:
            return []
        wav16 = self._resample_to_16k(audio, sr)
        c = self._l2norm(np.asarray(centroid, dtype=np.float32))
        if c.size == 0:
            return []
        cells = self._label_cells(
            wav16,
            c,
            win_step_ms=win_step_ms,
            win_len_ms=win_len_ms,
            win_threshold_high=win_threshold_high,
            win_threshold_low=win_threshold_low,
            debounce_count=debounce_count,
        )
        return [
            {
                "start_ms": int(cell["start_sample"] * 1000 / TARGET_SR),
                "end_ms": int(cell["end_sample"] * 1000 / TARGET_SR),
                "score": round(float(cell["score"]), 6),
                "label": cell["label"],
            }
            for cell in cells
        ]

    def filter_owner_track(
        self,
        audio_bytes: bytes,
        centroid: list[float],
        *,
        win_step_ms: int = DEFAULT_WIN_STEP_MS,
        win_len_ms: int = DEFAULT_WIN_LEN_MS,
        win_threshold_high: float = DEFAULT_WINDOW_HIGH,
        win_threshold_low: float = DEFAULT_WINDOW_LOW,
        min_owner_duration_ms: int = 300,
        debounce_count: int = 1,
    ) -> tuple[bytes | None, list[dict], int]:
        audio, sr = self._decode_audio_bytes(audio_bytes)
        if audio is None or len(audio) < MIN_AUDIO_SAMPLES:
            return None, [], 0
        wav16 = self._resample_to_16k(audio, sr)
        c = self._l2norm(np.asarray(centroid, dtype=np.float32))
        if c.size == 0:
            return None, [], 0
        cells = self._label_cells(
            wav16,
            c,
            win_step_ms=win_step_ms,
            win_len_ms=win_len_ms,
            win_threshold_high=win_threshold_high,
            win_threshold_low=win_threshold_low,
            debounce_count=debounce_count,
        )
        if not cells:
            return None, [], 0

        min_owner_samples = int(TARGET_SR * max(120, min_owner_duration_ms) / 1000)
        owner_segments = self._segments_from_cells(
            cells,
            label="owner",
            min_segment_samples=min_owner_samples,
        )
        if not owner_segments:
            skipped = self._skipped_from_owner_segments([], len(wav16), cells)
            return None, skipped, 0
        merge_gap_samples = int(TARGET_SR * OWNER_MERGE_GAP_MS / 1000)
        owner_segments = self._merge_close_segments(owner_segments, merge_gap_samples)
        pre_pad_samples = int(TARGET_SR * OWNER_PRE_PAD_MS / 1000)
        post_pad_samples = int(TARGET_SR * OWNER_POST_PAD_MS / 1000)
        owner_segments = self._expand_segments(
            owner_segments,
            len(wav16),
            pre_pad_samples=pre_pad_samples,
            post_pad_samples=post_pad_samples,
        )
        skipped = self._skipped_from_owner_segments(owner_segments, len(wav16), cells)
        owner_chunks: list[np.ndarray] = []
        join_silence = np.zeros(int(TARGET_SR * OWNER_JOIN_SILENCE_MS / 1000), dtype=np.float32)
        for seg in owner_segments:
            chunk = wav16[int(seg["start_sample"]) : int(seg["end_sample"])]
            if len(chunk) <= 0:
                continue
            if owner_chunks and len(join_silence) > 0:
                owner_chunks.append(join_silence)
            owner_chunks.append(chunk)
        if not owner_chunks:
            return None, skipped, 0
        owner = np.concatenate(owner_chunks, axis=0).astype(np.float32)
        owner_duration_ms = int(len(owner) * 1000 / TARGET_SR)
        if owner_duration_ms < max(120, min_owner_duration_ms):
            return None, skipped, owner_duration_ms
        return self._pcm_f32_to_wav_bytes(owner, TARGET_SR), skipped, owner_duration_ms

    def _extract_embedding_camplus(self, wav16: np.ndarray) -> np.ndarray | None:
        engine = self._load_engine()
        if engine is None:
            return None

        tmp_path: str | None = None
        try:
            wav_bytes = self._pcm_f32_to_wav_bytes(wav16, TARGET_SR)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tf.write(wav_bytes)
                tmp_path = tf.name

            # ModelScope speaker-verification pipeline expects enroll+test pair.
            # Passing same clip for both allows extracting speaker embedding output.
            result = engine([tmp_path, tmp_path], output_emb=True)
            emb = self._extract_first_embedding(result)
            if emb is None:
                raise ValueError("pipeline output does not contain usable embedding")
            self._backend = "cam++-modelscope-local"
            self._load_error = None
            return emb
        except Exception as e:
            self._load_error = f"{type(e).__name__}: {e}"
            self._backend = "cam++-unavailable"
            logger.warning("SV CAM++ inference failed: %s", e)
            return None
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _sv_unavailable_message(self) -> str:
        detail = self._load_error or "CAM++ model is not loaded"
        return f"CAM++ speaker verification model unavailable: {detail}"

    @staticmethod
    def _extract_first_embedding(result: Any) -> np.ndarray | None:
        if isinstance(result, dict):
            embs = result.get("embs")
            if isinstance(embs, list) and embs:
                for candidate in embs:
                    vec = SvService._coerce_vector(candidate)
                    if vec is not None:
                        return vec
            for v in result.values():
                vec = SvService._extract_first_embedding(v)
                if vec is not None:
                    return vec
            return None
        if isinstance(result, (list, tuple)):
            for item in result:
                vec = SvService._extract_first_embedding(item)
                if vec is not None:
                    return vec
        return SvService._coerce_vector(result)

    @staticmethod
    def _coerce_vector(value: Any) -> np.ndarray | None:
        if isinstance(value, np.ndarray):
            if value.ndim == 1 and value.size >= 16:
                return SvService._l2norm(value.astype(np.float32))
            if value.ndim == 2 and value.shape[0] >= 1 and value.shape[1] >= 16:
                return SvService._l2norm(value[0].astype(np.float32))
            return None
        if isinstance(value, (list, tuple)) and value:
            try:
                arr = np.asarray(value, dtype=np.float32)
            except Exception:
                return None
            if arr.ndim == 1 and arr.size >= 16 and np.all(np.isfinite(arr)):
                return SvService._l2norm(arr)
            if (
                arr.ndim == 2
                and arr.shape[0] >= 1
                and arr.shape[1] >= 16
                and np.all(np.isfinite(arr))
            ):
                return SvService._l2norm(arr[0])
        return None

    def _label_cells(
        self,
        wav16: np.ndarray,
        centroid: np.ndarray,
        *,
        win_step_ms: int,
        win_len_ms: int,
        win_threshold_high: float,
        win_threshold_low: float,
        debounce_count: int,
    ) -> list[dict]:
        step, win = self._window_params(win_step_ms, win_len_ms)
        starts = self._window_starts(len(wav16), step, win)
        if not starts:
            return []

        scores: list[float] = []
        raw_labels: list[str] = []
        for start in starts:
            segment = wav16[start : min(len(wav16), start + win)]
            if len(segment) < MIN_AUDIO_SAMPLES:
                score = -1.0
                label = "other"
            else:
                emb = self._extract_embedding_camplus(segment)
                if emb is None:
                    raise RuntimeError(self._sv_unavailable_message())
                score = float(np.clip(np.dot(self._l2norm(emb), centroid), -1.0, 1.0))
                label = "score_only"
            scores.append(score)
            raw_labels.append(label)

        labels = self._hysteresis_labels(
            scores,
            high=float(win_threshold_high),
            low=float(win_threshold_low),
            exit_below_low_count=OWNER_EXIT_BELOW_LOW_COUNT,
        )
        labels = self._smooth_labels(labels, debounce_count)
        centers = [min(len(wav16), int(start) + win // 2) for start in starts]
        bounds = [0]
        for idx in range(1, len(centers)):
            bounds.append((centers[idx - 1] + centers[idx]) // 2)
        bounds.append(len(wav16))

        cells: list[dict] = []
        for idx in range(len(starts)):
            start = max(0, min(int(bounds[idx]), len(wav16)))
            end = max(start, min(int(bounds[idx + 1]), len(wav16)))
            if end <= start:
                continue
            cells.append(
                {
                    "start_sample": start,
                    "end_sample": end,
                    "score": scores[idx],
                    "label": labels[idx],
                }
            )
        return cells

    @staticmethod
    def _window_params(win_step_ms: int, win_len_ms: int) -> tuple[int, int]:
        step = max(1, int(TARGET_SR * max(80, win_step_ms) / 1000))
        win = max(step, int(TARGET_SR * max(160, win_len_ms) / 1000))
        return step, win

    @staticmethod
    def _window_starts(total_samples: int, step: int, win: int) -> list[int]:
        if total_samples <= 0:
            return []
        if total_samples <= win:
            return [0]
        last_start = max(0, total_samples - win)
        starts = list(range(0, last_start + 1, step))
        if not starts or starts[-1] != last_start:
            starts.append(last_start)
        return sorted(set(starts))

    @staticmethod
    def _smooth_labels(labels: list[str], debounce_count: int) -> list[str]:
        min_run = max(1, int(debounce_count))
        if min_run <= 1 or len(labels) <= 1:
            return labels
        out = list(labels)

        def runs(values: list[str]) -> list[tuple[str, int, int]]:
            result: list[tuple[str, int, int]] = []
            start = 0
            for idx in range(1, len(values) + 1):
                if idx == len(values) or values[idx] != values[start]:
                    result.append((values[start], start, idx))
                    start = idx
            return result

        changed = True
        while changed:
            changed = False
            rs = runs(out)
            for idx, (label, start, end) in enumerate(rs):
                if end - start >= min_run:
                    continue
                prev_label = rs[idx - 1][0] if idx > 0 else None
                next_label = rs[idx + 1][0] if idx + 1 < len(rs) else None
                # Asymmetric smoothing:
                # We only collapse short owner islands into surrounding other.
                # Never collapse short other into owner (that causes intruder leakage).
                if (
                    label == "owner"
                    and prev_label is not None
                    and prev_label == next_label
                    and prev_label == "other"
                ):
                    for pos in range(start, end):
                        out[pos] = prev_label
                    changed = True

        for label, start, end in runs(out):
            if label == "owner" and end - start < min_run:
                for pos in range(start, end):
                    out[pos] = "other"
        return out

    @staticmethod
    def _hysteresis_labels(
        scores: list[float],
        *,
        high: float,
        low: float,
        exit_below_low_count: int,
    ) -> list[str]:
        """
        Speaker windows are noisy at word boundaries. Use two-threshold hysteresis:
        high enters owner, low keeps owner, and only repeated below-low windows exit.
        """
        labels: list[str] = []
        state = "other"
        below_low_run = 0
        exit_after = max(1, int(exit_below_low_count))
        for score in scores:
            if state == "other":
                if score >= high:
                    state = "owner"
                    below_low_run = 0
                labels.append(state)
                continue

            if score < low:
                below_low_run += 1
                if below_low_run >= exit_after:
                    state = "other"
                    labels.append("other")
                else:
                    labels.append("owner")
                continue

            below_low_run = 0
            labels.append("owner")
        return labels

    @staticmethod
    def _segments_from_cells(
        cells: list[dict],
        *,
        label: str,
        min_segment_samples: int = 0,
    ) -> list[dict]:
        segments: list[dict] = []
        start: int | None = None
        end = 0
        scores: list[float] = []

        def flush() -> None:
            nonlocal start, end, scores
            if start is None:
                return
            if end - start >= max(0, min_segment_samples):
                segments.append(
                    {
                        "start_sample": start,
                        "end_sample": end,
                        "score": float(np.mean(scores)) if scores else 0.0,
                    }
                )
            start = None
            end = 0
            scores = []

        for cell in cells:
            if cell["label"] == label:
                cell_start = int(cell["start_sample"])
                cell_end = int(cell["end_sample"])
                if start is None:
                    start = cell_start
                    end = cell_end
                    scores = [float(cell["score"])]
                elif cell_start <= end:
                    end = max(end, cell_end)
                    scores.append(float(cell["score"]))
                else:
                    flush()
                    start = cell_start
                    end = cell_end
                    scores = [float(cell["score"])]
            else:
                flush()
        flush()
        return segments

    @staticmethod
    def _merge_close_segments(segments: list[dict], max_gap_samples: int) -> list[dict]:
        if not segments:
            return []
        ordered = sorted(segments, key=lambda s: int(s["start_sample"]))
        merged: list[dict] = [dict(ordered[0])]
        for seg in ordered[1:]:
            last = merged[-1]
            gap = int(seg["start_sample"]) - int(last["end_sample"])
            if gap <= max(0, max_gap_samples):
                last["end_sample"] = max(int(last["end_sample"]), int(seg["end_sample"]))
                last["score"] = max(float(last.get("score", 0.0)), float(seg.get("score", 0.0)))
            else:
                merged.append(dict(seg))
        return merged

    @staticmethod
    def _skipped_from_owner_segments(
        owner_segments: list[dict],
        total_samples: int,
        cells: list[dict],
    ) -> list[dict]:
        skipped: list[dict] = []
        cursor = 0
        for seg in owner_segments:
            start = max(cursor, min(total_samples, int(seg["start_sample"])))
            end = max(start, min(total_samples, int(seg["end_sample"])))
            if start > cursor:
                skipped.append(
                    {
                        "start_ms": int(cursor * 1000 / TARGET_SR),
                        "end_ms": int(start * 1000 / TARGET_SR),
                        "score": round(SvService._mean_cell_score(cells, cursor, start), 6),
                        "reason": "non_owner",
                    }
                )
            cursor = max(cursor, end)
        if cursor < total_samples:
            skipped.append(
                {
                    "start_ms": int(cursor * 1000 / TARGET_SR),
                    "end_ms": int(total_samples * 1000 / TARGET_SR),
                    "score": round(SvService._mean_cell_score(cells, cursor, total_samples), 6),
                    "reason": "non_owner",
                }
            )
        return skipped

    @staticmethod
    def _mean_cell_score(cells: list[dict], start_sample: int, end_sample: int) -> float:
        scores = [
            float(cell["score"])
            for cell in cells
            if int(cell["end_sample"]) > start_sample and int(cell["start_sample"]) < end_sample
        ]
        return float(np.mean(scores)) if scores else 0.0

    @staticmethod
    def _expand_segments(
        segments: list[dict],
        total_samples: int,
        *,
        pre_pad_samples: int,
        post_pad_samples: int,
    ) -> list[dict]:
        if not segments or (pre_pad_samples <= 0 and post_pad_samples <= 0):
            return segments
        ordered = sorted(segments, key=lambda s: int(s["start_sample"]))
        expanded: list[dict] = []
        for idx, seg in enumerate(ordered):
            raw_start = int(seg["start_sample"])
            raw_end = int(seg["end_sample"])
            start = max(0, raw_start - max(0, pre_pad_samples))
            end = min(total_samples, raw_end + max(0, post_pad_samples))
            if idx > 0:
                prev_end = int(ordered[idx - 1]["end_sample"])
                start = max(start, (prev_end + raw_start) // 2)
            if idx + 1 < len(ordered):
                next_start = int(ordered[idx + 1]["start_sample"])
                end = min(end, (raw_end + next_start) // 2)
            if end <= start:
                continue
            if expanded and start <= expanded[-1]["end_sample"]:
                expanded[-1]["end_sample"] = max(expanded[-1]["end_sample"], end)
                expanded[-1]["score"] = max(float(expanded[-1]["score"]), float(seg.get("score", 0.0)))
            else:
                expanded.append(
                    {
                        "start_sample": start,
                        "end_sample": end,
                        "score": float(seg.get("score", 0.0)),
                    }
                )
        return expanded
    @staticmethod
    def _decode_audio_bytes(audio_bytes: bytes) -> tuple[np.ndarray | None, int]:
        try:
            import soundfile as sf

            data, fs = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
            if isinstance(data, np.ndarray) and data.ndim > 1:
                data = data.mean(axis=1)
            return np.asarray(data, dtype=np.float32), int(fs)
        except Exception:
            pass
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                channels = wf.getnchannels()
                rate = wf.getframerate() or TARGET_SR
                frames = wf.readframes(wf.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16)
                if channels > 1:
                    audio = audio.reshape(-1, channels).mean(axis=1)
                return (audio.astype(np.float32) / 32768.0), int(rate)
        except Exception:
            return None, 0

    @staticmethod
    def _resample_to_16k(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if sample_rate == TARGET_SR or sample_rate <= 0:
            return audio.astype(np.float32)
        target_len = max(1, int(len(audio) * TARGET_SR / sample_rate))
        x_old = np.linspace(0.0, 1.0, len(audio), dtype=np.float64)
        x_new = np.linspace(0.0, 1.0, target_len, dtype=np.float64)
        return np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)

    @staticmethod
    def _l2norm(vec: np.ndarray) -> np.ndarray:
        if vec.ndim != 1:
            vec = vec.reshape(-1)
        denom = float(np.linalg.norm(vec) + 1e-8)
        if denom <= 0:
            return vec.astype(np.float32)
        return (vec / denom).astype(np.float32)

    @staticmethod
    def _pcm_f32_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()


