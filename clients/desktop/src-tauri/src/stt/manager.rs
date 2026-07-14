//! 全天候语音 Pipeline 总控：采集 → 重采样 → VAD 截断 → 发射 STT_AUDIO_READY。

#![cfg(feature = "ambient")]
#![allow(dead_code)]

use super::audio_capture::start_capture;
use super::audio_processor::AudioProcessor;
use super::endpointing::EndpointingMachine;
use super::vad_engine::SileroVadEngine;
use crate::jvs::process_manager::JvsHandle;
use base64::Engine;
use crossbeam_channel::unbounded;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use tauri::{AppHandle, Emitter, Manager};

const STT_SAMPLE_RATE: u32 = 16000;
const CHUNK_LEN: usize = 512;
/// 10 * 32ms
const MIN_PTT_CHUNKS: usize = 10;

fn ptt_stream_finalize_timeout(buffer_chunks: usize, has_partial_text: bool) -> std::time::Duration {
    let ms = if has_partial_text {
        1500
    } else if buffer_chunks <= 45 {
        1500
    } else {
        900
    };
    std::time::Duration::from_millis(ms)
}

/// 截断完成时向前端/上层发送的载荷：16kHz 单声道 WAV 的 base64。
#[derive(Clone, serde::Serialize)]
pub struct SttAudioPayload {
    pub wav_base64: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recognized_text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recognized_finalized: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recognized_source: Option<String>,
}

/// PTT 未能产出可识别音频（过短、采音失败、麦克风未写入等）。
#[derive(Clone, serde::Serialize)]
pub struct SttPttFailedPayload {
    pub reason: String,
    pub chunks: usize,
    pub detail: String,
}

fn emit_ptt_failed(app: &AppHandle, reason: &str, chunks: usize, detail: &str) {
    let payload = SttPttFailedPayload {
        reason: reason.to_string(),
        chunks,
        detail: detail.to_string(),
    };
    let _ = app.emit("STT_PTT_FAILED", payload.clone());
    crate::l3_spawn::write_voice_chat_trace(
        "ptt",
        "ptt.failed",
        reason,
        &format!("chunks={} {}", chunks, detail),
    );
    crate::l3_spawn::write_voice_companion_debug(
        "rust",
        "ptt.failed",
        reason,
        &format!("chunks={} {}", chunks, detail),
    );
}

fn pcm_f32_to_wav(samples: &[f32], sample_rate: u32) -> Vec<u8> {
    use std::io::Write;
    let mut buf = Vec::new();
    let num_channels = 1u16;
    let bits_per_sample = 16u16;
    let byte_rate = sample_rate * num_channels as u32 * bits_per_sample as u32 / 8;
    let block_align = num_channels * bits_per_sample / 8;
    let data_size = samples.len() * 2;

    let _ = buf.write_all(b"RIFF");
    let _ = buf.write_all(&(36 + data_size as u32).to_le_bytes());
    let _ = buf.write_all(b"WAVE");
    let _ = buf.write_all(b"fmt ");
    let _ = buf.write_all(&16u32.to_le_bytes());
    let _ = buf.write_all(&1u16.to_le_bytes());
    let _ = buf.write_all(&num_channels.to_le_bytes());
    let _ = buf.write_all(&sample_rate.to_le_bytes());
    let _ = buf.write_all(&byte_rate.to_le_bytes());
    let _ = buf.write_all(&block_align.to_le_bytes());
    let _ = buf.write_all(&bits_per_sample.to_le_bytes());
    let _ = buf.write_all(b"data");
    let _ = buf.write_all(&(data_size as u32).to_le_bytes());
    for &s in samples {
        let clamped = (s * 32767.0).clamp(-32768.0, 32767.0) as i16;
        let _ = buf.write_all(&clamped.to_le_bytes());
    }
    buf
}

fn resolve_jvs_base_url(app: &AppHandle) -> String {
    app.try_state::<Arc<JvsHandle>>()
        .map(|h| h.status().base_url.clone())
        .unwrap_or_else(|| "http://127.0.0.1:18982".to_string())
}

#[derive(Clone)]
pub enum PttCaptureOutcome {
    Ready(SttAudioPayload),
    Failed(SttPttFailedPayload),
}

/// PTT 按住说话：按下即采，松开立即 `finalize_ptt` 并发射 `STT_AUDIO_READY`。
pub fn run_ptt_capture(
    app_handle: AppHandle,
    model_path: PathBuf,
    finalize_rx: std::sync::mpsc::Receiver<()>,
    running: Arc<AtomicBool>,
) -> PttCaptureOutcome {
    let app = Arc::new(app_handle);
    let stream_session_id = format!(
        "ptt-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0)
    );
    let jvs_base_url = resolve_jvs_base_url(&app);
    let mut stream_client = super::stream_stt_client::take_prewarmed(
        &jvs_base_url,
        std::time::Duration::from_secs(45),
    )
    .or_else(|| {
        super::stream_stt_client::SttStreamClient::connect(
            &jvs_base_url,
            &stream_session_id,
        )
        .ok()
    });
    let (stream, rx_raw, sample_rate, source_channels) = match start_capture() {
        Ok(v) => v,
        Err(e) => {
            emit_ptt_failed(&app, "mic_open_failed", 0, &e);
            return PttCaptureOutcome::Failed(SttPttFailedPayload {
                reason: "mic_open_failed".to_string(),
                chunks: 0,
                detail: e.clone(),
            });
        }
    };
    let (tx_chunk, rx_chunk) = unbounded::<[f32; CHUNK_LEN]>();

    let vad_engine = match SileroVadEngine::new(&model_path) {
        Ok(v) => v,
        Err(e) => {
            let msg = format!("VAD 初始化失败: {e}");
            emit_ptt_failed(&app, "vad_init_failed", 0, &msg);
            return PttCaptureOutcome::Failed(SttPttFailedPayload {
                reason: "vad_init_failed".to_string(),
                chunks: 0,
                detail: msg,
            });
        }
    };
    let mut endpointing = EndpointingMachine::new(vad_engine);
    endpointing.begin_ptt();

    let running_p = Arc::clone(&running);
    let running_e = Arc::clone(&running);

    let join_processor = thread::spawn(move || {
        if let Ok(mut processor) = AudioProcessor::new(sample_rate, source_channels) {
            let _ = processor.process_stream(&rx_raw, &running_p, |chunk| {
                let _ = tx_chunk.send(chunk);
            });
        }
    });

    let mut chunks_received: usize = 0;
    while running_e.load(Ordering::Relaxed) {
        if finalize_rx.try_recv().is_ok() {
            break;
        }
        match rx_chunk.recv_timeout(std::time::Duration::from_millis(100)) {
            Ok(chunk) => {
                chunks_received += 1;
                if let Some(client) = stream_client.as_mut() {
                    let _ = client.push_chunk(&chunk);
                }
                let _ = endpointing.feed_chunk_ptt(&chunk);
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
        }
    }

    running_e.store(false, Ordering::Relaxed);
    let buffer_chunks = endpointing.ptt_buffer_chunks();
    if let Some(audio) = endpointing.finalize_ptt() {
        let wav = pcm_f32_to_wav(&audio, STT_SAMPLE_RATE);
        let wav_base64 = base64::engine::general_purpose::STANDARD.encode(&wav);
        let has_partial_text = stream_client
            .as_ref()
            .map(|c| c.has_text())
            .unwrap_or(false);
        let stream_timeout = ptt_stream_finalize_timeout(buffer_chunks, has_partial_text);
        let stream_result = stream_client.as_mut().map(|c| c.finalize(stream_timeout));
        let recognized_text = stream_result
            .as_ref()
            .and_then(|r| r.text.as_ref())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        let recognized_finalized = recognized_text
            .as_ref()
            .map(|_| stream_result.as_ref().map(|r| r.finalized).unwrap_or(false));
        let recognized_source = recognized_text.as_ref().map(|_| {
            if recognized_finalized.unwrap_or(false) {
                "jvs_stream_final".to_string()
            } else {
                "jvs_stream_partial_timeout".to_string()
            }
        });
        if recognized_finalized != Some(true) {
            crate::l3_spawn::write_voice_chat_trace(
                "ptt",
                "stream_fallback_local",
                stream_client
                    .as_ref()
                    .map(|c| c.session_id())
                    .unwrap_or(&stream_session_id),
                &format!(
                    "reason=stream_final_miss has_partial={} buffer_chunks={} timeout_ms={} partial_count={} final_count={} audio_frames_sent={}",
                    has_partial_text,
                    buffer_chunks,
                    stream_timeout.as_millis(),
                    stream_result.as_ref().map(|r| r.partial_count).unwrap_or(0),
                    stream_result.as_ref().map(|r| r.final_count).unwrap_or(0),
                    stream_result.as_ref().map(|r| r.audio_frames_sent).unwrap_or(0)
                ),
            );
        }
        crate::l3_spawn::write_voice_chat_trace(
            "ptt",
            "ptt.audio_ready",
            "emit STT_AUDIO_READY",
            &format!(
                "chunks_rx={} buffer_chunks={} wav_bytes={} stream_text_len={} stream_finalized={}",
                chunks_received,
                buffer_chunks,
                wav.len(),
                recognized_text.as_ref().map(|s| s.len()).unwrap_or(0),
                recognized_finalized.unwrap_or(false)
            ),
        );
        let payload = SttAudioPayload {
            wav_base64,
            recognized_text,
            recognized_finalized,
            recognized_source,
        };
        let _ = app.emit("STT_AUDIO_READY", payload.clone());
        drop(stream);
        let _ = join_processor.join();
        return PttCaptureOutcome::Ready(payload);
    } else {
        let detail = if chunks_received == 0 {
            "麦克风未写入任何音频帧（可能被其它管线占用或采音线程失败）".to_string()
        } else if buffer_chunks < MIN_PTT_CHUNKS {
            format!(
                "录音过短或有效语音不足（收到 {} 帧，至少需要 {} 帧 ≈320ms）",
                buffer_chunks, MIN_PTT_CHUNKS
            )
        } else {
            format!(
                "finalize 未产出音频（chunks_rx={} buffer_chunks={}）",
                chunks_received, buffer_chunks
            )
        };
        emit_ptt_failed(&app, "no_audio", buffer_chunks, &detail);
        drop(stream);
        let _ = join_processor.join();
        return PttCaptureOutcome::Failed(SttPttFailedPayload {
            reason: "no_audio".to_string(),
            chunks: buffer_chunks,
            detail,
        });
    }
}

/// 启动全天候监听：麦克风 → 重采样 → 512 切片 → VAD 截断，截断完成时发射 `STT_AUDIO_READY`。
/// 返回的 guard 持有音频流；drop 后停止采集与 pipeline。
pub fn start_listening(
    app_handle: AppHandle,
    model_path: PathBuf,
) -> Result<ListeningGuard, String> {
    let (stream, rx_raw, sample_rate, source_channels) = start_capture()?;
    let (tx_chunk, rx_chunk) = unbounded::<[f32; CHUNK_LEN]>();

    let vad_engine = SileroVadEngine::new(&model_path)?;
    let mut endpointing = EndpointingMachine::new(vad_engine);
    let app = Arc::new(app_handle);
    let running = Arc::new(AtomicBool::new(true));
    let running_p = Arc::clone(&running);
    let running_e = Arc::clone(&running);

    let join_processor = thread::spawn(move || {
        if let Ok(mut processor) = AudioProcessor::new(sample_rate, source_channels) {
            let _ = processor.process_stream(&rx_raw, &running_p, |chunk| {
                let _ = tx_chunk.send(chunk);
            });
        }
    });

    let join_endpointing = thread::spawn(move || {
        while running_e.load(Ordering::Relaxed) {
            match rx_chunk.recv_timeout(std::time::Duration::from_millis(200)) {
                Ok(chunk) => {
                    if let Ok(Some(audio)) = endpointing.feed_chunk(&chunk) {
                        let wav = pcm_f32_to_wav(&audio, STT_SAMPLE_RATE);
                        let wav_base64 = base64::engine::general_purpose::STANDARD.encode(&wav);
                        let _ = app.emit(
                            "STT_AUDIO_READY",
                            SttAudioPayload {
                                wav_base64,
                                recognized_text: None,
                                recognized_finalized: None,
                                recognized_source: None,
                            },
                        );
                    }
                }
                Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
                Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
            }
        }
    });

    Ok(ListeningGuard {
        _stream: stream,
        running,
        _join_processor: Some(join_processor),
        _join_endpointing: Some(join_endpointing),
    })
}

/// 持有音频流与 pipeline 线程；drop 时停止采集并等待线程结束。
pub struct ListeningGuard {
    _stream: cpal::Stream,
    running: Arc<AtomicBool>,
    _join_processor: Option<thread::JoinHandle<()>>,
    _join_endpointing: Option<thread::JoinHandle<()>>,
}

impl Drop for ListeningGuard {
    fn drop(&mut self) {
        self.running.store(false, Ordering::Relaxed);
        if let Some(h) = self._join_processor.take() {
            let _ = h.join();
        }
        if let Some(h) = self._join_endpointing.take() {
            let _ = h.join();
        }
    }
}
