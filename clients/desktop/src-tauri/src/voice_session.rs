//! 陪伴语音会话相位（供唤醒管线 VAD 打断与 Masking 判断）。

use std::sync::atomic::{AtomicU8, Ordering};

const PHASE_IDLE: u8 = 0;
const PHASE_LISTENING: u8 = 1;
const PHASE_THINKING: u8 = 2;
const PHASE_SPEAKING: u8 = 3;

static COMPANION_PHASE: AtomicU8 = AtomicU8::new(PHASE_IDLE);

pub fn set_companion_phase(phase: &str) {
    let v = match phase.trim().to_lowercase().as_str() {
        "listening" => PHASE_LISTENING,
        "thinking" => PHASE_THINKING,
        "speaking" => PHASE_SPEAKING,
        _ => PHASE_IDLE,
    };
    COMPANION_PHASE.store(v, Ordering::Relaxed);
}

#[allow(dead_code)]
pub fn companion_phase_is_thinking_or_speaking() -> bool {
    matches!(
        COMPANION_PHASE.load(Ordering::Relaxed),
        PHASE_THINKING | PHASE_SPEAKING
    )
}

#[allow(dead_code)]
pub fn should_monitor_barge_in(session_active: bool) -> bool {
    session_active
        && (crate::voice_playback::is_playing() || companion_phase_is_thinking_or_speaking())
}
