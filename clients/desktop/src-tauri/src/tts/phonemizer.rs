//! Phonemizer trait - 文本转音素
//!
//! 由于 Rust 的 espeak-ng 绑定较复杂，设计此 trait 支持 Split-Inference：
//! - 本地可实现：直接转换
//! - 本地不可用：将文本发给 Tier 2 处理成 Phoneme，再回传 Layer 3 合成

use async_trait::async_trait;

/// 音素处理结果
#[derive(Debug, Clone)]
pub struct PhonemeResult {
    /// 音素序列（如 IPA 或 Kokoro 所需格式）
    pub phonemes: Vec<String>,
    /// Kokoro token IDs（若 Tier 2 返回则直接使用，避免客户端 vocab 映射）
    pub tokens: Option<Vec<i64>>,
    /// 是否由远程（Tier 2）处理
    pub from_remote: bool,
}

/// Phonemizer trait - 文本转音素
///
/// 实现者可以是：
/// - 本地 espeak-ng 绑定（若可用）
/// - 远程 Tier 2 代理（Split-Inference 模式）
#[async_trait]
pub trait Phonemizer: Send + Sync {
    /// 将文本转换为音素
    /// style: 如 "zm" 用于中英混合
    async fn text_to_phonemes(&self, text: &str, style: &str) -> Result<PhonemeResult, String>;

    /// 是否支持本地处理
    fn is_local_available(&self) -> bool;
}

/// 远程 Phonemizer - 调用 Tier 2 进行音素转换（Split-Inference）
pub struct RemotePhonemizer {
    tier2_base_url: String,
}

impl RemotePhonemizer {
    pub fn new(tier2_base_url: impl Into<String>) -> Self {
        Self {
            tier2_base_url: tier2_base_url.into(),
        }
    }
}

#[async_trait]
impl Phonemizer for RemotePhonemizer {
    async fn text_to_phonemes(&self, text: &str, _style: &str) -> Result<PhonemeResult, String> {
        let client = reqwest::Client::new();
        let url = format!(
            "{}/api/v2/voice/phonemize",
            self.tier2_base_url.trim_end_matches('/')
        );
        let res = client
            .post(&url)
            .json(&serde_json::json!({ "text": text, "style": "zm" }))
            .send()
            .await
            .map_err(|e| e.to_string())?;

        if !res.status().is_success() {
            return Err(format!("Tier 2 phonemize failed: {}", res.status()));
        }

        let body: serde_json::Value = res.json().await.map_err(|e| e.to_string())?;
        let phonemes: Vec<String> = body
            .get("phonemes")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();

        let tokens: Option<Vec<i64>> = body
            .get("tokens")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_i64()).collect())
            .filter(|v: &Vec<i64>| !v.is_empty());

        Ok(PhonemeResult {
            phonemes,
            tokens,
            from_remote: true,
        })
    }

    fn is_local_available(&self) -> bool {
        false
    }
}
