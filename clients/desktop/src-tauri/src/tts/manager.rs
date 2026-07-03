//! SpeechEngine - TTS 统一入口与自检
//!
//! 当前策略：优先调用本地 voice_server(/v1/tts/synthesize, MOSS ONNX)；
//! 若失败则按配置回退云端 TTS。

use crate::tts::cloud_adapter::{AliyunTtsConfig, CloudAliyunAdapter};
use crate::tts::model_manager::{ModelManager, DEFAULT_VOICE_SERVER_URL};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use sysinfo::System;

/// TTS 自检结果
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TtsSelfCheckResult {
    pub arch_ok: bool,
    pub memory_ok: bool,
    pub compute_ok: bool,
    pub local_enabled: bool,
    pub reason: Option<String>,
}

/// TTS 提供者枚举
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderEnum {
    /// Local Voice Server (MOSS ONNX)
    LocalVoiceServer,
    /// Cloud Aliyun Qwen/CosyVoice
    Cloud,
}

/// 文本长度阈值，超过则优先直接走云端，避免本地长文本推理阻塞。
const CLOUD_TEXT_LENGTH_THRESHOLD: usize = 500;

/// 最小可用内存 (bytes)
const MIN_FREE_RAM_BYTES: u64 = 1024 * 1024 * 1024; // 1GB

/// SpeechEngine - 统一 TTS 入口
pub struct SpeechEngine {
    self_check: TtsSelfCheckResult,
    cloud: Option<CloudAliyunAdapter>,
    voice_server_base_url: String,
}

impl SpeechEngine {
    /// 创建并执行自检（不加载模型，仅自检）
    pub fn new(
        voice_server_base_url: impl Into<String>,
        model_dir: Option<PathBuf>,
        aliyun_config: Option<AliyunTtsConfig>,
    ) -> Self {
        let self_check = Self::run_self_check();
        let voice_server_base_url = voice_server_base_url.into();
        let _ = model_dir;

        let cloud = aliyun_config.map(CloudAliyunAdapter::new);

        Self {
            self_check,
            cloud,
            voice_server_base_url,
        }
    }

    /// 创建 ModelManager（使用 app_data_dir）
    pub fn model_manager(
        voice_server_url: Option<&str>,
        app_data_dir: Option<PathBuf>,
    ) -> ModelManager {
        ModelManager::new(
            voice_server_url.unwrap_or(DEFAULT_VOICE_SERVER_URL),
            app_data_dir.map(|d| d.join("tts")),
        )
    }

    /// 执行 TTS 自检：检测系统架构 (Arch) 和 内存 (RAM)
    fn run_self_check() -> TtsSelfCheckResult {
        let arch = std::env::consts::ARCH;
        let arch_ok = arch == "x86_64" || arch == "aarch64";

        let mut sys = System::new_all();
        sys.refresh_memory();
        let free_ram = sys.available_memory();
        let memory_ok = free_ram >= MIN_FREE_RAM_BYTES;

        let compute_ok = arch_ok && memory_ok;
        let local_enabled = compute_ok;

        let reason = if !arch_ok {
            Some(format!("Unsupported arch: {}", arch))
        } else if !memory_ok {
            Some(format!(
                "Insufficient RAM: {} MB free (need >= 1GB)",
                free_ram / (1024 * 1024)
            ))
        } else if !compute_ok {
            Some("Compute benchmark failed".to_string())
        } else {
            None
        };

        TtsSelfCheckResult {
            arch_ok,
            memory_ok,
            compute_ok,
            local_enabled,
            reason,
        }
    }

    /// 获取自检结果
    pub fn self_check_result(&self) -> &TtsSelfCheckResult {
        &self.self_check
    }

    /// 根据文本决定使用哪个 Provider
    pub fn decide_provider(&self, text: &str) -> ProviderEnum {
        if text.len() > CLOUD_TEXT_LENGTH_THRESHOLD {
            return ProviderEnum::Cloud;
        }
        ProviderEnum::LocalVoiceServer
    }

    /// 合成语音（按 Fallback Chain 顺序尝试）
    pub async fn speak(&self, text: &str) -> Result<Vec<u8>, String> {
        let provider = self.decide_provider(text);

        match provider {
            ProviderEnum::LocalVoiceServer => self.try_voice_server_then_cloud(text).await,
            ProviderEnum::Cloud => self.try_cloud(text).await,
        }
    }

    async fn try_voice_server_then_cloud(&self, text: &str) -> Result<Vec<u8>, String> {
        match self.call_local_voice_server_tts(text).await {
            Ok(audio) => Ok(audio),
            Err(e) => {
                if let Some(ref cloud) = self.cloud {
                    cloud.synthesize(text, "zh-CN-XiaoxiaoNeural").await
                } else {
                    Err(format!("Voice server failed: {}; Cloud not configured", e))
                }
            }
        }
    }

    async fn call_local_voice_server_tts(&self, text: &str) -> Result<Vec<u8>, String> {
        let client = reqwest::Client::new();
        let url = format!(
            "{}/v1/tts/synthesize",
            self.voice_server_base_url.trim_end_matches('/')
        );

        let res = client
            .post(&url)
            .json(&serde_json::json!({
                "text": text,
                "voice": "Junhao"
            }))
            .send()
            .await
            .map_err(|e| e.to_string())?;

        if !res.status().is_success() {
            return Err(format!("voice_server TTS failed: {}", res.status()));
        }

        let bytes = res.bytes().await.map_err(|e| e.to_string())?;
        Ok(bytes.to_vec())
    }

    async fn try_cloud(&self, text: &str) -> Result<Vec<u8>, String> {
        if let Some(ref cloud) = self.cloud {
            cloud.synthesize(text, "zh-CN-XiaoxiaoNeural").await
        } else {
            Err("Cloud TTS not configured".to_string())
        }
    }
}
