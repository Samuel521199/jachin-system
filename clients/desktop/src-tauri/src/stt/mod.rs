//! STT (Speech-to-Text) 模块
//!
//! 唤醒模式 (Wake-Up)：轻量级 KWS，检测到唤醒词后发出 WAKE_UP 事件。
//! 后续可接入 openWakeWord (ONNX) 或 oww-rs 实现真实唤醒词检测。

mod keyword_spotting;

pub use keyword_spotting::WakeWordDetector;
