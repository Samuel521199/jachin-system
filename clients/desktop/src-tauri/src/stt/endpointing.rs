//! 智能截断状态机：结合 VAD 概率与尾音/最大时长，决定何时交出完整录音。

#![cfg(feature = "ambient")]
#![allow(dead_code)]

use super::vad_engine::SileroVadEngine;

/// 32ms/帧
const VAD_THRESHOLD: f32 = 0.5;
/// 打断接话阶段略降低起句门限，避免句首被吃
const VAD_THRESHOLD_REARM: f32 = 0.25;
/// 检测到说话起点后再往前多留 ~320ms
const RING_PREROLL_CHUNKS: usize = 10;
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

    pub fn reset(&mut self) {
        self.state = RecordingState::Idle;
        self.audio_buffer.clear();
        self.silence_frames = 0;
        self.total_frames = 0;
        self.vad_engine.reset_states();
    }

    /// PTT 按下：立即进入采集，不等待 VAD 起句。
    pub fn begin_ptt(&mut self) {
        self.reset();
        self.state = RecordingState::Speaking;
    }

    /// PTT 按住期间：持续写入缓冲，不做尾音截断。
    pub fn feed_chunk_ptt(&mut self, chunk: &[f32]) -> Result<(), String> {
        if chunk.len() != CHUNK_LEN {
            return Err(format!("chunk 长度须为 {}，当前 {}", CHUNK_LEN, chunk.len()));
        }
        if self.state != RecordingState::Speaking {
            self.begin_ptt();
        }
        self.audio_buffer.extend_from_slice(chunk);
        self.total_frames += 1;
        Ok(())
    }

    /// PTT 缓冲内已有多少帧（每帧 CHUNK_LEN 样本）。
    pub fn ptt_buffer_chunks(&self) -> usize {
        self.audio_buffer.len() / CHUNK_LEN
    }

    /// PTT 松开：立即交出整段（仍受最短有效语音约束）。
    pub fn finalize_ptt(&mut self) -> Option<Vec<f32>> {
        if self.state != RecordingState::Speaking {
            self.reset();
            return None;
        }
        self.state = RecordingState::Idle;
        self.vad_engine.reset_states();
        let out = if self.ptt_buffer_chunks() >= MIN_SPEECH_FRAMES {
            Some(std::mem::take(&mut self.audio_buffer))
        } else {
            self.audio_buffer.clear();
            None
        };
        self.silence_frames = 0;
        self.total_frames = 0;
        out
    }

    /// 打断后把环形缓冲拼进当前句；`rearm` 时用更低门限 + 前滚，避免起首被吃。
    pub fn seed_from_ring(
        &mut self,
        ring: &[[f32; CHUNK_LEN]],
        rearm: bool,
    ) -> Result<(), String> {
        self.reset();
        if ring.is_empty() {
            return Ok(());
        }

        let thresh = if rearm {
            VAD_THRESHOLD_REARM
        } else {
            VAD_THRESHOLD
        };
        let preroll = if rearm {
            RING_PREROLL_CHUNKS
        } else {
            4
        };

        let mut onset = 0usize;
        for (i, chunk) in ring.iter().enumerate() {
            let prob = self.vad_engine.process_chunk(chunk)?;
            if prob > thresh {
                onset = i.saturating_sub(preroll);
                break;
            }
        }

        self.state = RecordingState::Speaking;
        self.silence_frames = 0;
        for chunk in &ring[onset..] {
            self.audio_buffer.extend_from_slice(chunk);
        }
        self.total_frames = (self.audio_buffer.len() / CHUNK_LEN).max(1);
        Ok(())
    }

    /// 喂入一帧 512 样本。若截断完成且满足最短语音长度，返回 `Some(完整 PCM)`，否则返回 `None`。
    pub fn feed_chunk(&mut self, chunk: &[f32]) -> Result<Option<Vec<f32>>, String> {
        if chunk.len() != CHUNK_LEN {
            return Err(format!("chunk 长度须为 {}，当前 {}", CHUNK_LEN, chunk.len()));
        }
        let prob = self.vad_engine.process_chunk(chunk)?;
        self.feed_chunk_with_prob(chunk, prob, false)
    }

    /// 已计算 VAD 概率时调用，避免同一帧重复跑模型。
    /// `rearm`：接话阶段使用更低起句门限。
    pub fn feed_chunk_with_prob(
        &mut self,
        chunk: &[f32],
        prob: f32,
        rearm: bool,
    ) -> Result<Option<Vec<f32>>, String> {
        if chunk.len() != CHUNK_LEN {
            return Err(format!("chunk 长度须为 {}，当前 {}", CHUNK_LEN, chunk.len()));
        }

        let speech_thresh = if rearm {
            VAD_THRESHOLD_REARM
        } else {
            VAD_THRESHOLD
        };

        match self.state {
            RecordingState::Idle => {
                if prob > speech_thresh {
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
                if prob > speech_thresh {
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
