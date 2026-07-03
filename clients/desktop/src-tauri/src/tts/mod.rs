//! TTS (Text-to-Speech) 模块
//!
//! 实现 Tier 3 自适应 TTS 策略：
//! - Local Voice Server (MOSS ONNX) -> Cloud (Aliyun)

#![allow(dead_code)]

mod cloud_adapter;
mod kokoro_vocab;
mod local_kokoro;
mod manager;
mod model_manager;
mod phonemizer;

/// TTS 提供者 trait - 统一合成接口
#[async_trait::async_trait]
pub trait TTSProvider: Send + Sync {
    /// 合成语音，返回 PCM/WAV 字节
    async fn synthesize(&self, text: &str, style: &str) -> Result<Vec<u8>, String>;

    /// 是否可用
    fn is_available(&self) -> bool;
}

pub use cloud_adapter::AliyunTtsConfig;
pub use manager::SpeechEngine;
pub use model_manager::ProgressCallback;
