//! 智能截断状态机：结合 VAD 概率与尾音/最大时长，决定何时交出完整录音。

#![cfg(feature = "ambient")]
#![allow(dead_code)]

use super::vad_engine::SileroVadEngine;

/// 32ms/帧
const VAD_THRESHOLD: f32 = 0.5;
/// 25 * 32ms = 800ms 尾音静音
const MAX_SILENCE_FRAMES: usize = 25;
/// 468 * 32ms ≈ 15000ms 最大录音
const MAX_TOTAL_FRAMES: usize = 468;
/// 10 * 32ms = 320ms 最短有效语音
const MIN_SPEECH_FRAMES: usize = 10;
const CHUNK_LEN: usize = 512;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RecordingState {
    Idle,
    Speaking,
}

pub struct EndpointingMachine {
    pub vad_engine: SileroVadEngine,
    pub state: RecordingState,
    pub audio_buffer: Vec<f32>,
    pub silence_frames: usize,
    pub total_frames: usize,
}

impl EndpointingMachine {
    pub fn new(vad_engine: SileroVadEngine) -> Self {
        Self {
            vad_engine,
            state: RecordingState::Idle,
            audio_buffer: Vec::new(),
            silence_frames: 0,
            total_frames: 0,
        }
    }

    /// 喂入一帧 512 样本。若截断完成且满足最短语音长度，返回 `Some(完整 PCM)`，否则返回 `None`。
    pub fn feed_chunk(&mut self, chunk: &[f32]) -> Result<Option<Vec<f32>>, String> {
        if chunk.len() != CHUNK_LEN {
            return Err(format!("chunk 长度须为 {}，当前 {}", CHUNK_LEN, chunk.len()));
        }

        let prob = self.vad_engine.process_chunk(chunk)?;

        match self.state {
            RecordingState::Idle => {
                if prob > VAD_THRESHOLD {
                    self.state = RecordingState::Speaking;
                    self.audio_buffer.clear();
                    self.audio_buffer.extend_from_slice(chunk);
                    self.silence_frames = 0;
                    self.total_frames = 1;
                }
                Ok(None)
            }
            RecordingState::Speaking => {
                self.audio_buffer.extend_from_slice(chunk);
                self.total_frames += 1;
                if prob > VAD_THRESHOLD {
                    self.silence_frames = 0;
                } else {
                    self.silence_frames += 1;
                }

                let should_end =
                    self.silence_frames >= MAX_SILENCE_FRAMES
                        || self.total_frames >= MAX_TOTAL_FRAMES;

                if should_end {
                    self.state = RecordingState::Idle;
                    self.vad_engine.reset_states();
                    let out = if self.audio_buffer.len() / CHUNK_LEN >= MIN_SPEECH_FRAMES {
                        Some(std::mem::take(&mut self.audio_buffer))
                    } else {
                        self.audio_buffer.clear();
                        None
                    };
                    self.silence_frames = 0;
                    self.total_frames = 0;
                    return Ok(out);
                }
                Ok(None)
            }
        }
    }
}
