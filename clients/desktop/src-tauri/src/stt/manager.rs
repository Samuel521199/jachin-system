//! 全天候语音 Pipeline 总控：采集 → 重采样 → VAD 截断 → 发射 STT_AUDIO_READY。

#![cfg(feature = "ambient")]
#![allow(dead_code)]

use super::audio_capture::start_capture;
use super::audio_processor::AudioProcessor;
use super::endpointing::EndpointingMachine;
use super::vad_engine::SileroVadEngine;
use base64::Engine;
use crossbeam_channel::unbounded;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use tauri::{AppHandle, Emitter};

const STT_SAMPLE_RATE: u32 = 16000;
const CHUNK_LEN: usize = 512;

/// 截断完成时向前端/上层发送的载荷：16kHz 单声道 WAV 的 base64。
#[derive(Clone, serde::Serialize)]
pub struct SttAudioPayload {
    pub wav_base64: String,
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

/// 启动全天候监听：麦克风 → 重采样 → 512 切片 → VAD 截断，截断完成时发射 `STT_AUDIO_READY`。
/// 返回的 guard 持有音频流；drop 后停止采集与 pipeline。
pub fn start_listening(
    app_handle: AppHandle,
    model_path: PathBuf,
) -> Result<ListeningGuard, String> {
    let (stream, rx_raw, sample_rate) = start_capture()?;
    let (tx_chunk, rx_chunk) = unbounded::<[f32; CHUNK_LEN]>();

    let vad_engine = SileroVadEngine::new(&model_path)?;
    let mut endpointing = EndpointingMachine::new(vad_engine);
    let app = Arc::new(app_handle);
    let running = Arc::new(AtomicBool::new(true));
    let running_p = Arc::clone(&running);
    let running_e = Arc::clone(&running);

    let join_processor = thread::spawn(move || {
        let source_channels = 1;
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
                        let _ = app.emit("STT_AUDIO_READY", SttAudioPayload { wav_base64 });
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
