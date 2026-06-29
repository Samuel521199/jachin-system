#![cfg(feature = "ambient")]

use base64::Engine;
use serde::Deserialize;
use std::fs;
use std::path::PathBuf;

const DEFAULT_WAKE_HIGH: f32 = 0.45;
const DEFAULT_WINDOW_HIGH: f32 = 0.38;

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct VoiceprintWakeGate {
    pub threshold_high: Option<f32>,
    pub threshold_low: Option<f32>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct VoiceprintWindowLabel {
    pub win_threshold_high: Option<f32>,
    pub win_threshold_low: Option<f32>,
    pub win_step_ms: Option<u32>,
    pub win_len_ms: Option<u32>,
    pub min_owner_duration_ms: Option<u32>,
    pub debounce_count: Option<u32>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct OwnerVoiceprintProfile {
    pub version: Option<u32>,
    pub model_id: Option<String>,
    pub centroid: Vec<f32>,
    pub wake_gate: Option<VoiceprintWakeGate>,
    pub window_label: Option<VoiceprintWindowLabel>,
}

impl OwnerVoiceprintProfile {
    pub fn wake_threshold_high(&self) -> f32 {
        self.wake_gate
            .as_ref()
            .and_then(|g| g.threshold_high)
            .unwrap_or(DEFAULT_WAKE_HIGH)
    }

    pub fn window_threshold_high(&self) -> f32 {
        self.window_label
            .as_ref()
            .and_then(|w| w.win_threshold_high)
            .unwrap_or(DEFAULT_WINDOW_HIGH)
    }
}

#[derive(Debug, Clone)]
pub struct VerifyResult {
    pub score: f32,
    pub is_match: bool,
}

#[derive(Debug, Deserialize)]
struct VerifyJson {
    score: Option<f32>,
    is_match: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct SkippedSegmentJson {
    pub start_ms: Option<u32>,
    pub end_ms: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct FilterOwnerTrackJson {
    owner_wav_b64: Option<String>,
    owner_duration_ms: Option<u32>,
    skipped_segments: Option<Vec<SkippedSegmentJson>>,
}

pub struct OwnerTrackFilterResult {
    pub wav: Option<Vec<u8>>,
    pub owner_duration_ms: u32,
    pub skipped_segments_count: usize,
}

fn owner_voiceprint_path() -> Option<PathBuf> {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").ok()
    } else {
        std::env::var("HOME").ok()
    }?;
    Some(
        PathBuf::from(home)
            .join(".jachin")
            .join("voice")
            .join("owner_voiceprint.json"),
    )
}

pub fn load_owner_voiceprint_profile() -> Result<Option<OwnerVoiceprintProfile>, String> {
    let Some(path) = owner_voiceprint_path() else {
        return Ok(None);
    };
    if !path.exists() {
        return Ok(None);
    }
    let s = fs::read_to_string(&path).map_err(|e| format!("读取 owner_voiceprint.json 失败: {e}"))?;
    let parsed: OwnerVoiceprintProfile =
        serde_json::from_str(&s).map_err(|e| format!("解析 owner_voiceprint.json 失败: {e}"))?;
    if parsed.centroid.is_empty() {
        return Err("owner_voiceprint.json centroid 为空".to_string());
    }
    Ok(Some(parsed))
}

pub fn jvs_verify_blocking(
    base_url: &str,
    wav: &[u8],
    profile: &OwnerVoiceprintProfile,
    threshold: f32,
) -> Result<VerifyResult, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(12))
        .build()
        .map_err(|e| e.to_string())?;
    let part = reqwest::blocking::multipart::Part::bytes(wav.to_vec())
        .mime_str("audio/wav")
        .map_err(|e| e.to_string())?
        .file_name("speech.wav");
    let form = reqwest::blocking::multipart::Form::new()
        .part("audio", part)
        .text(
            "centroid",
            serde_json::to_string(&profile.centroid).map_err(|e| e.to_string())?,
        )
        .text("threshold", format!("{threshold:.4}"));
    let url = format!("{}/v1/sv/verify", base_url.trim_end_matches('/'));
    let resp = client.post(&url).multipart(form).send().map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("JVS SV verify status {}", resp.status()));
    }
    let json: VerifyJson = resp.json().map_err(|e| e.to_string())?;
    let score = json.score.unwrap_or(0.0);
    let is_match = json.is_match.unwrap_or(score >= threshold);
    Ok(VerifyResult { score, is_match })
}

pub fn jvs_filter_owner_track_blocking(
    base_url: &str,
    wav: &[u8],
    profile: &OwnerVoiceprintProfile,
) -> Result<OwnerTrackFilterResult, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())?;
    let part = reqwest::blocking::multipart::Part::bytes(wav.to_vec())
        .mime_str("audio/wav")
        .map_err(|e| e.to_string())?
        .file_name("speech.wav");
    let mut form = reqwest::blocking::multipart::Form::new()
        .part("audio", part)
        .text(
            "centroid",
            serde_json::to_string(&profile.centroid).map_err(|e| e.to_string())?,
        );
    if let Some(w) = &profile.window_label {
        if let Some(v) = w.win_step_ms {
            form = form.text("win_step_ms", v.to_string());
        }
        if let Some(v) = w.win_len_ms {
            form = form.text("win_len_ms", v.to_string());
        }
        if let Some(v) = w.win_threshold_high {
            form = form.text("win_threshold_high", format!("{v:.4}"));
        }
        if let Some(v) = w.win_threshold_low {
            form = form.text("win_threshold_low", format!("{v:.4}"));
        }
        if let Some(v) = w.min_owner_duration_ms {
            form = form.text("min_owner_duration_ms", v.to_string());
        }
        if let Some(v) = w.debounce_count {
            form = form.text("debounce_count", v.to_string());
        }
    }
    let url = format!("{}/v1/sv/filter_owner_track", base_url.trim_end_matches('/'));
    let resp = client.post(&url).multipart(form).send().map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("JVS SV filter_owner_track status {}", resp.status()));
    }
    let json: FilterOwnerTrackJson = resp.json().map_err(|e| e.to_string())?;
    let owner_b64 = json.owner_wav_b64.unwrap_or_default();
    let owner_duration_ms = json.owner_duration_ms.unwrap_or(0);
    let skipped_segments_count = json.skipped_segments.as_ref().map(|v| v.len()).unwrap_or(0);
    if owner_b64.trim().is_empty() || owner_duration_ms == 0 {
        return Ok(OwnerTrackFilterResult {
            wav: None,
            owner_duration_ms,
            skipped_segments_count,
        });
    }
    let wav = base64::engine::general_purpose::STANDARD
        .decode(owner_b64.trim().as_bytes())
        .map_err(|e| format!("decode owner_wav_b64 失败: {e}"))?;
    if wav.is_empty() {
        return Ok(OwnerTrackFilterResult {
            wav: None,
            owner_duration_ms,
            skipped_segments_count,
        });
    }
    Ok(OwnerTrackFilterResult {
        wav: Some(wav),
        owner_duration_ms,
        skipped_segments_count,
    })
}
