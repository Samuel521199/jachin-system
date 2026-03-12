//! STT (Speech-to-Text) 模块
//!
//! 唤醒模式 (Wake-Up)：轻量级 KWS，检测到唤醒词后发出 WAKE_UP 事件。
//! 后续可接入 openWakeWord (ONNX) 或 oww-rs 实现真实唤醒词检测。

mod keyword_spotting;

#[cfg(feature = "ambient")]
mod audio_capture;
#[cfg(feature = "ambient")]
pub(crate) mod commands;
#[cfg(feature = "ambient")]
mod audio_processor;
#[cfg(feature = "ambient")]
mod endpointing;
#[cfg(feature = "ambient")]
mod manager;
#[cfg(feature = "ambient")]
mod vad_engine;

pub use keyword_spotting::WakeWordDetector;

#[cfg(feature = "ambient")]
#[allow(unused_imports)]
pub use audio_capture::start_capture;
#[cfg(feature = "ambient")]
#[allow(unused_imports)]
pub use audio_processor::AudioProcessor;
#[cfg(feature = "ambient")]
#[allow(unused_imports)]
pub use endpointing::{EndpointingMachine, RecordingState};
#[cfg(feature = "ambient")]
#[allow(unused_imports)]
pub use manager::{start_listening, ListeningGuard, SttAudioPayload};
#[cfg(feature = "ambient")]
#[allow(unused_imports)]
pub use vad_engine::SileroVadEngine;
#[cfg(feature = "ambient")]
pub use commands::SttState;
