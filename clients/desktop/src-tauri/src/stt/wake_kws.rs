//! 唤醒词检测：无 Porcupine 原生库时，用 JVS STT 对短窗音频做短语匹配（支持用户自定义 wake_word）。

#![cfg(feature = "ambient")]

use std::time::{Duration, Instant};

const WINDOW_SAMPLES: usize = 32000; // 2s @ 16kHz（响应更快，仍覆盖长中文唤醒句）
const MIN_POLL_INTERVAL: Duration = Duration::from_millis(1000);
const MIN_RMS: f32 = 0.003;

pub struct SttAssistedKws {
    wake_word: String,
    window: Vec<f32>,
    last_poll: Instant,
}

impl SttAssistedKws {
    pub fn new(wake_word: String) -> Self {
        Self {
            wake_word: wake_word.trim().to_string(),
            window: Vec::with_capacity(WINDOW_SAMPLES),
            last_poll: Instant::now() - MIN_POLL_INTERVAL,
        }
    }

    pub fn wake_word(&self) -> &str {
        &self.wake_word
    }

    /// 喂入 512 样本。若应发起一次 STT 轮询，返回待 STT 的 PCM 窗口副本。
    pub fn feed(&mut self, chunk: &[f32; 512]) -> Option<Vec<f32>> {
        for &s in chunk {
            if self.window.len() >= WINDOW_SAMPLES {
                self.window.remove(0);
            }
            self.window.push(s);
        }

        if self.window.len() < WINDOW_SAMPLES {
            return None;
        }
        if self.last_poll.elapsed() < MIN_POLL_INTERVAL {
            return None;
        }

        let rms = rms(&self.window);
        if rms < MIN_RMS {
            return None;
        }

        self.last_poll = Instant::now();
        Some(self.window.clone())
    }
}

pub fn normalize_wake_text(s: &str) -> String {
    s.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric() || *c > '\u{4e00}')
        .collect()
}

/// 判断 STT 文本是否命中唤醒句（子串匹配 + 常见别名）。
pub fn transcript_matches_wake(transcript: &str, wake_word: &str) -> bool {
    let t = normalize_wake_text(transcript);
    let w = normalize_wake_text(wake_word);
    if w.is_empty() || t.is_empty() {
        return false;
    }
    if t.contains(&w) || w.contains(&t) {
        return true;
    }
    // 长唤醒句：连续子串命中（STT 常漏字）
    let w_chars: Vec<char> = w.chars().collect();
    let min_sub = (w_chars.len() / 2).max(3).min(4).min(w_chars.len());
    if min_sub > 0 {
        for i in 0..=w_chars.len().saturating_sub(min_sub) {
            let sub: String = w_chars[i..i + min_sub].iter().collect();
            if t.contains(&sub) {
                return true;
            }
        }
    }
    // 开发联调：用户设 Jachin 时可说 jarvis（Porcupine 内置词近似）
    let w_lower = wake_word.to_lowercase();
    if w_lower.contains("jachin") && t.contains("jarvis") {
        return true;
    }
    if w_lower.contains("jarvis") && t.contains("jarvis") {
        return true;
    }
    false
}

fn rms(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let sum = samples.iter().map(|x| x * x).sum::<f32>();
    (sum / samples.len() as f32).sqrt()
}
