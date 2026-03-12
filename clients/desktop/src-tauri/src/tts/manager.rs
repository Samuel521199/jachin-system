//! SpeechEngine - TTS 统一入口与自检
//!
//! 实现 Capability Check (自检)：检测系统架构 (Arch) 和 内存 (RAM)。

use crate::tts::cloud_adapter::{AliyunTtsConfig, CloudAliyunAdapter};
use crate::tts::local_kokoro::{LocalKokoroEngine, KOKORO_STYLE_ZM};
use crate::tts::model_manager::{ModelManager, DEFAULT_TIER2_URL};
#[cfg(feature = "tts-local")]
use crate::tts::phonemizer::RemotePhonemizer;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
#[cfg(feature = "tts-local")]
use std::sync::Arc;
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
    /// Local Kokoro ONNX
    Local,
    /// Tier 2 XTTS (Edge)
    Edge,
    /// Cloud Aliyun Qwen/CosyVoice
    Cloud,
}

/// 文本长度阈值，超过则用 Edge
const EDGE_TEXT_LENGTH_THRESHOLD: usize = 500;

/// 最小可用内存 (bytes)
const MIN_FREE_RAM_BYTES: u64 = 1024 * 1024 * 1024; // 1GB

/// SpeechEngine - 统一 TTS 入口
pub struct SpeechEngine {
    self_check: TtsSelfCheckResult,
    local: Option<LocalKokoroEngine>,
    cloud: Option<CloudAliyunAdapter>,
    tier2_base_url: String,
}

impl SpeechEngine {
    /// 创建并执行自检（不加载模型，仅自检）
    pub fn new(
        tier2_base_url: impl Into<String>,
        model_dir: Option<PathBuf>,
        aliyun_config: Option<AliyunTtsConfig>,
    ) -> Self {
        let self_check = Self::run_self_check();
        let tier2_base_url = tier2_base_url.into();

        let local = if self_check.local_enabled {
            #[cfg(feature = "tts-local")]
            {
                model_dir.and_then(|dir| {
                    let model_path = dir.join("kokoro-v0_19.onnx");
                    let voices_path = dir.join("voices.json");
                    if model_path.exists() {
                        let mut engine = LocalKokoroEngine::new(model_path, voices_path);
                        engine.set_phonemizer(Arc::new(RemotePhonemizer::new(&tier2_base_url)));
                        let _ = engine.load();
                        Some(engine)
                    } else {
                        None
                    }
                })
            }
            #[cfg(not(feature = "tts-local"))]
            {
                let _ = model_dir;
                None
            }
        } else {
            None
        };

        let cloud = aliyun_config.map(CloudAliyunAdapter::new);

        Self {
            self_check,
            local,
            cloud,
            tier2_base_url,
        }
    }

    /// 创建 ModelManager（使用 app_data_dir）
    pub fn model_manager(tier2_url: Option<&str>, app_data_dir: Option<PathBuf>) -> ModelManager {
        ModelManager::new(
            tier2_url.unwrap_or(DEFAULT_TIER2_URL),
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
        let local_enabled = arch_ok && memory_ok && compute_ok;

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
        if self.self_check.local_enabled
            && self.local.is_some()
            && text.len() <= EDGE_TEXT_LENGTH_THRESHOLD
        {
            return ProviderEnum::Local;
        }

        if text.len() > EDGE_TEXT_LENGTH_THRESHOLD {
            return ProviderEnum::Edge;
        }

        if !self.self_check.local_enabled || self.local.is_none() {
            return ProviderEnum::Edge;
        }

        ProviderEnum::Cloud
    }

    /// 合成语音（按 Fallback Chain 顺序尝试）
    pub async fn speak(&self, text: &str) -> Result<Vec<u8>, String> {
        let provider = self.decide_provider(text);

        match provider {
            ProviderEnum::Local => {
                if let Some(ref engine) = self.local {
                    engine.synthesize(text, KOKORO_STYLE_ZM).await
                } else {
                    self.try_edge_then_cloud(text).await
                }
            }
            ProviderEnum::Edge => self.try_edge_then_cloud(text).await,
            ProviderEnum::Cloud => self.try_cloud(text).await,
        }
    }

    async fn try_edge_then_cloud(&self, text: &str) -> Result<Vec<u8>, String> {
        match self.call_tier2_tts(text).await {
            Ok(audio) => Ok(audio),
            Err(e) => {
                if let Some(ref cloud) = self.cloud {
                    cloud.synthesize(text, "zh-CN-XiaoxiaoNeural").await
                } else {
                    Err(format!("Tier 2 failed: {}; Cloud not configured", e))
                }
            }
        }
    }

    async fn call_tier2_tts(&self, text: &str) -> Result<Vec<u8>, String> {
        let client = reqwest::Client::new();
        let url = format!("{}/api/v2/voice/synthesize", self.tier2_base_url.trim_end_matches('/'));

        let res = client
            .post(&url)
            .json(&serde_json::json!({
                "text": text,
                "voice": "zh-CN-XiaoxiaoNeural",
                "language": "zh-CN"
            }))
            .send()
            .await
            .map_err(|e| e.to_string())?;

        if !res.status().is_success() {
            return Err(format!("Tier 2 TTS failed: {}", res.status()));
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
