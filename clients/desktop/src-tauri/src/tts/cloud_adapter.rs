//! Cloud TTS 适配器 - 阿里云 Qwen/CosyVoice
//!
//! 作为 Fallback Chain 的最后兜底，当 Local 和 Tier 2 均不可用时调用。

use reqwest::Client;
use serde::{Deserialize, Serialize};

/// 阿里云 TTS 配置
#[derive(Debug, Clone)]
pub struct AliyunTtsConfig {
    pub api_key: String,
    pub api_secret: Option<String>,
    /// 端点，如 https://dashscope.aliyuncs.com
    pub endpoint: String,
}

/// 阿里云语音合成请求
#[derive(Debug, Serialize)]
struct AliyunSynthesizeRequest {
    model: String,
    input: AliyunInput,
    parameters: AliyunParameters,
}

#[derive(Debug, Serialize)]
struct AliyunInput {
    text: String,
}

#[derive(Debug, Serialize)]
struct AliyunParameters {
    voice: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    sample_rate: Option<u32>,
}

/// 阿里云响应（简化）
#[derive(Debug, Deserialize)]
struct AliyunSynthesizeResponse {
    output: Option<AliyunOutput>,
}

#[derive(Debug, Deserialize)]
struct AliyunOutput {
    audio_url: Option<String>,
    #[serde(rename = "task_id")]
    task_id: Option<String>,
}

/// Cloud Aliyun TTS 适配器
pub struct CloudAliyunAdapter {
    config: AliyunTtsConfig,
    client: Client,
}

impl CloudAliyunAdapter {
    pub fn new(config: AliyunTtsConfig) -> Self {
        Self {
            config,
            client: Client::new(),
        }
    }

    /// 合成语音（REST API）
    pub async fn synthesize(&self, text: &str, voice: &str) -> Result<Vec<u8>, String> {
        let url = format!("{}/api/v1/services/audio/tts/speech", self.config.endpoint);

        let body = AliyunSynthesizeRequest {
            model: "cosyvoice-v1".to_string(),
            input: AliyunInput {
                text: text.to_string(),
            },
            parameters: AliyunParameters {
                voice: voice.to_string(),
                sample_rate: Some(22050),
            },
        };

        let res = self
            .client
            .post(&url)
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .json(&body)
            .send()
            .await
            .map_err(|e| e.to_string())?;

        if !res.status().is_success() {
            let status = res.status();
            let err_text = res.text().await.unwrap_or_default();
            return Err(format!("Aliyun TTS failed {}: {}", status, err_text));
        }

        let bytes = res.bytes().await.map_err(|e| e.to_string())?;
        Ok(bytes.to_vec())
    }

    /// WebSocket 流式合成（可选，用于长文本）- 待实现
    #[allow(dead_code)]
    pub async fn synthesize_stream(&self, _text: &str, _voice: &str) -> Result<Vec<u8>, String> {
        Err("WebSocket stream not yet implemented".to_string())
    }
}
