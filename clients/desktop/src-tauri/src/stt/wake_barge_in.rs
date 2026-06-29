//! VAD 人声打断检测（Barge-in 主路径）。

#![cfg(feature = "ambient")]

use std::time::{Duration, Instant};

const BASE_THRESHOLD: f32 = 0.5;
const ELEVATED_MULT: f32 = 1.5;
/// 连续人声帧数（约 32ms/帧 via 512@16kHz）≥7 ≈ 224ms
const MIN_SPEECH_FRAMES: u32 = 7;
const COOLDOWN_MS: u64 = 450;

pub struct BargeInDetector {
    speech_frames: u32,
    cooldown_until: Option<Instant>,
}

impl BargeInDetector {
    pub fn new() -> Self {
        Self {
            speech_frames: 0,
            cooldown_until: None,
        }
    }

    pub fn reset(&mut self) {
        self.speech_frames = 0;
        self.cooldown_until = None;
    }

    pub fn set_cooldown(&mut self, duration: Duration) {
        self.speech_frames = 0;
        self.cooldown_until = Some(Instant::now() + duration);
    }

    /// `elevated`：出声期间阈值抬高（Masking 阶段）。
    pub fn feed(&mut self, vad_prob: f32, elevated: bool) -> bool {
        if let Some(until) = self.cooldown_until {
            if Instant::now() < until {
                self.speech_frames = 0;
                return false;
            }
            self.cooldown_until = None;
        }

        let thresh = if elevated {
            BASE_THRESHOLD * ELEVATED_MULT
        } else {
            BASE_THRESHOLD
        };

        if vad_prob > thresh {
            self.speech_frames += 1;
            if self.speech_frames >= MIN_SPEECH_FRAMES {
                self.speech_frames = 0;
                self.cooldown_until = Some(Instant::now() + Duration::from_millis(COOLDOWN_MS));
                return true;
            }
        } else {
            self.speech_frames = 0;
        }
        false
    }
}
