//! Local Kokoro ONNX TTS 引擎
//!
//! 使用 ort crate 加载 ONNX 模型，处理多语言混合需经 Phonemizer。
//! 若本地无法 Phoneme 处理，则降级为 Split-Inference：Tier 2 处理音素，Layer 3 合成。
//!
//! 中英混合优化：强制 Language Code 为 "z" (Chinese)，自动处理夹杂的英文单词。
//! 不切换模型，只切换 Style 向量（如 zm）。

#[cfg(feature = "tts-local")]
use crate::tts::kokoro_vocab;
use crate::tts::phonemizer::{PhonemeResult, Phonemizer};
use std::path::PathBuf;
use std::sync::Arc;
#[cfg(feature = "tts-local")]
use std::sync::Mutex;

/// Kokoro Language Code：z = Chinese，可自动处理夹杂的英文单词
pub const KOKORO_LANGUAGE_CODE: &str = "z";

/// Kokoro 语音风格（zm 用于中英混合，只切换 Style 向量不换模型）
pub const KOKORO_STYLE_ZM: &str = "zm";

/// 采样率 24kHz
const SAMPLE_RATE: u32 = 24000;

/// x86_64 默认推理线程数
#[cfg(not(target_arch = "aarch64"))]
const INFERENCE_INTRA_OP_THREADS: i32 = 0; // 0 = 自动（按物理核数）

/// aarch64（树莓派等 ARM）降低线程数，避免资源争抢
#[cfg(target_arch = "aarch64")]
const INFERENCE_INTRA_OP_THREADS: i32 = 2;

/// Local Kokoro ONNX 引擎
pub struct LocalKokoroEngine {
    model_path: PathBuf,
    voices_path: PathBuf,
    phonemizer: Option<Arc<dyn Phonemizer>>,
    #[cfg(feature = "tts-local")]
    session: Option<Mutex<ort::session::Session>>,
    #[cfg(feature = "tts-local")]
    /// Style 向量 (N, 256)，按 len(tokens) 索引
    style_vectors: Vec<Vec<f32>>,
}

impl LocalKokoroEngine {
    pub fn new(model_path: PathBuf, voices_path: PathBuf) -> Self {
        Self {
            model_path,
            voices_path,
            phonemizer: None,
            #[cfg(feature = "tts-local")]
            session: None,
            #[cfg(feature = "tts-local")]
            style_vectors: Vec::new(),
        }
    }

    /// 设置 Phonemizer（本地或远程）
    pub fn set_phonemizer(&mut self, p: Arc<dyn Phonemizer>) {
        self.phonemizer = Some(p);
    }

    /// 加载模型（需 tts-local feature，否则返回错误）
    /// aarch64 架构自动降低线程数配置以适配树莓派
    pub fn load(&mut self) -> Result<(), String> {
        if !self.model_path.exists() {
            return Err(format!("Model not found: {:?}", self.model_path));
        }

        #[cfg(feature = "tts-local")]
        {
            use ort::session::Session;
            let threads = INFERENCE_INTRA_OP_THREADS.max(0) as usize;
            let builder = Session::builder().map_err(|e| e.to_string())?;
            let builder = if threads > 0 {
                builder.with_intra_threads(threads).map_err(|e| e.to_string())?
            } else {
                builder
            };
            let session = builder
                .commit_from_file(&self.model_path)
                .map_err(|e| e.to_string())?;
            self.session = Some(Mutex::new(session));
            self.style_vectors = Self::load_style_vectors(&self.voices_path, KOKORO_STYLE_ZM)?;
            Ok(())
        }

        #[cfg(not(feature = "tts-local"))]
        {
            let _ = &self.voices_path;
            Err("tts-local feature not enabled. Build with --features tts-local".to_string())
        }
    }

    #[cfg(feature = "tts-local")]
    fn load_style_vectors(voices_path: &PathBuf, style: &str) -> Result<Vec<Vec<f32>>, String> {
        let dir = voices_path.parent().unwrap_or(voices_path.as_path());
        let zm_bin = dir.join(format!("{}.bin", style));

        if zm_bin.exists() {
            let data = std::fs::read(&zm_bin).map_err(|e| e.to_string())?;
            let floats: Vec<f32> = data
                .chunks_exact(4)
                .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
                .collect();
            if floats.len() % 256 != 0 {
                return Err("Invalid voices bin: size must be multiple of 256".to_string());
            }
            let vectors: Vec<Vec<f32>> = floats
                .chunks(256)
                .map(|c| c.to_vec())
                .collect();
            return Ok(vectors);
        }

        if voices_path.exists() {
            let content = std::fs::read_to_string(voices_path).map_err(|e| e.to_string())?;
            let json: serde_json::Value = serde_json::from_str(&content).map_err(|e| e.to_string())?;
            if let Some(voice_data) = json.get(style).or_else(|| json.get("voices").and_then(|v| v.get(style))) {
                if let Some(arr) = voice_data.as_array() {
                    let vectors: Vec<Vec<f32>> = arr
                        .iter()
                        .filter_map(|v| {
                            v.as_array().map(|a| {
                                a.iter().filter_map(|x| x.as_f64().map(|f| f as f32)).collect()
                            })
                        })
                        .filter(|v: &Vec<f32>| v.len() == 256)
                        .collect();
                    if !vectors.is_empty() {
                        return Ok(vectors);
                    }
                }
            }
        }

        Err(format!(
            "No style vectors for '{}'. Place {}.bin or voices.json in {:?}",
            style,
            style,
            dir
        ))
    }

    /// 合成语音
    /// 强制使用 Language Code "z" (Chinese)，自动处理中英混合；仅切换 Style 向量
    pub async fn synthesize(&self, text: &str, style: &str) -> Result<Vec<u8>, String> {
        let _ = (KOKORO_LANGUAGE_CODE, style);
        let phonemes = self.text_to_phonemes_inner(text, style).await?;

        #[cfg(feature = "tts-local")]
        {
            self.run_inference(&phonemes).await
        }

        #[cfg(not(feature = "tts-local"))]
        {
            let _ = phonemes;
            Err("tts-local feature not enabled".to_string())
        }
    }

    #[cfg(feature = "tts-local")]
    async fn run_inference(&self, phoneme_result: &PhonemeResult) -> Result<Vec<u8>, String> {
        let mut session_guard = self
            .session
            .as_ref()
            .ok_or("Model not loaded. Call load() first.")?
            .lock()
            .map_err(|_| "Session lock poisoned".to_string())?;

        let tokens: Vec<i64> = if let Some(ref t) = phoneme_result.tokens {
            t.clone()
        } else {
            kokoro_vocab::phonemes_to_tokens(&phoneme_result.phonemes)
        };

        if tokens.is_empty() {
            return Err("No tokens to synthesize".to_string());
        }
        if tokens.len() > 510 {
            return Err(format!(
                "Token sequence too long: {} (max 510)",
                tokens.len()
            ));
        }

        let style_idx = tokens.len().min(self.style_vectors.len().saturating_sub(1));
        let style_vec = self
            .style_vectors
            .get(style_idx)
            .ok_or("No style vector for this length")?;

        let input_ids: Vec<i64> = std::iter::once(0i64)
            .chain(tokens.into_iter())
            .chain(std::iter::once(0i64))
            .collect();
        let seq_len = input_ids.len();

        use ort::value::Tensor;
        let input_ids_tensor = Tensor::from_array(([1usize, seq_len], input_ids))
            .map_err(|e: ort::Error| e.to_string())?;
        let style_tensor = Tensor::from_array(([1usize, 256], style_vec.clone()))
            .map_err(|e: ort::Error| e.to_string())?;
        let speed_tensor = Tensor::from_array(([1usize], vec![1.0f32]))
            .map_err(|e: ort::Error| e.to_string())?;

        let outputs = session_guard
            .run(ort::inputs![
                "input_ids" => input_ids_tensor,
                "style" => style_tensor,
                "speed" => speed_tensor,
            ])
            .map_err(|e: ort::Error| e.to_string())?;

        let (_, samples_slice) = outputs[0]
            .try_extract_tensor::<f32>()
            .map_err(|e: ort::Error| e.to_string())?;
        let samples: Vec<f32> = samples_slice.to_vec();
        let wav = Self::pcm_f32_to_wav(&samples, SAMPLE_RATE);
        Ok(wav)
    }

    #[cfg(feature = "tts-local")]
    fn pcm_f32_to_wav(samples: &[f32], sample_rate: u32) -> Vec<u8> {
        use std::io::Write;
        let mut buf = Vec::new();
        let num_channels = 1u16;
        let bits_per_sample = 16u16;
        let byte_rate = sample_rate * num_channels as u32 * bits_per_sample as u32 / 8;
        let block_align = num_channels * bits_per_sample / 8;
        let data_size = samples.len() * 2;

        buf.write_all(b"RIFF").unwrap();
        buf.write_all(&(36 + data_size as u32).to_le_bytes()).unwrap();
        buf.write_all(b"WAVE").unwrap();
        buf.write_all(b"fmt ").unwrap();
        buf.write_all(&16u32.to_le_bytes()).unwrap();
        buf.write_all(&1u16.to_le_bytes()).unwrap();
        buf.write_all(&num_channels.to_le_bytes()).unwrap();
        buf.write_all(&sample_rate.to_le_bytes()).unwrap();
        buf.write_all(&byte_rate.to_le_bytes()).unwrap();
        buf.write_all(&block_align.to_le_bytes()).unwrap();
        buf.write_all(&bits_per_sample.to_le_bytes()).unwrap();
        buf.write_all(b"data").unwrap();
        buf.write_all(&(data_size as u32).to_le_bytes()).unwrap();
        for &s in samples {
            let clamped = (s * 32767.0).clamp(-32768.0, 32767.0) as i16;
            buf.write_all(&clamped.to_le_bytes()).unwrap();
        }
        buf
    }

    async fn text_to_phonemes_inner(
        &self,
        text: &str,
        style: &str,
    ) -> Result<PhonemeResult, String> {
        if let Some(ref p) = self.phonemizer {
            p.text_to_phonemes(text, style).await
        } else {
            Err(
                "No Phonemizer set. Use Split-Inference: send text to Tier 2 for phonemes."
                    .to_string(),
            )
        }
    }
}
