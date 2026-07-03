//! Settings Commands - 用户设置与运行时配置

use crate::config::UserSettings;
use crate::jvs::process_manager::JvsHandle;
use crate::kernel::{generate_policy, HardwareProfile, RuntimeConfig};
use base64::Engine;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tauri::Emitter;
use tauri::Manager;

/// 返回合并后的最终 RuntimeConfig（供 UI 显示当前生效状态）
#[tauri::command]
pub fn get_current_config() -> Result<RuntimeConfig, String> {
    let profile = HardwareProfile::detect();
    let settings = UserSettings::load();
    Ok(generate_policy(profile, &settings))
}

/// 返回 settings.json 的原始内容（供 UI 回显选项）
#[tauri::command]
pub fn get_user_settings() -> Result<UserSettings, String> {
    Ok(UserSettings::load())
}

/// 更新用户设置：前端传入完整 UserSettings，直接持久化并触发事件
#[tauri::command]
pub fn update_user_settings(app: tauri::AppHandle, patch: UserSettings) -> Result<(), String> {
    patch.save()?;
    app.emit(
        "settings-updated",
        SettingsUpdatedPayload {
            restart_required: true,
        },
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[derive(Debug, Deserialize)]
pub struct EnrollOwnerVoiceprintRequest {
    pub sample_wavs_base64: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct EnrollOwnerVoiceprintResponse {
    pub ok: bool,
    pub path: String,
    pub sample_count: usize,
    pub embedding_dim: usize,
}

#[derive(Debug, Serialize)]
struct OwnerVoiceprintWakeGate {
    threshold_high: f32,
    threshold_low: f32,
}

#[derive(Debug, Serialize)]
struct OwnerVoiceprintWindowLabel {
    win_threshold_high: f32,
    win_threshold_low: f32,
    win_step_ms: u32,
    win_len_ms: u32,
    min_owner_duration_ms: u32,
    debounce_count: u32,
}

#[derive(Debug, Serialize)]
struct OwnerVoiceprintProfile {
    version: u32,
    model_id: String,
    sample_count: usize,
    centroid: Vec<f32>,
    wake_gate: OwnerVoiceprintWakeGate,
    window_label: OwnerVoiceprintWindowLabel,
    created_at: String,
    updated_at: String,
}

fn resolve_jvs_base_url(app: &tauri::AppHandle) -> String {
    app.try_state::<Arc<JvsHandle>>()
        .map(|h| h.status().base_url.clone())
        .unwrap_or_else(|| "http://127.0.0.1:18982".to_string())
}

fn ensure_jvs_blocking(app: &tauri::AppHandle) -> Result<String, String> {
    let base_url = resolve_jvs_base_url(app);
    let health_url = format!("{}/health", base_url.trim_end_matches('/'));
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;
    if client
        .get(&health_url)
        .send()
        .and_then(|r| r.error_for_status())
        .is_ok()
    {
        return Ok(base_url);
    }
    let app_clone = app.clone();
    tauri::async_runtime::block_on(async move {
        crate::jvs::process_manager::start_jvs_process(&app_clone).await
    })?;
    Ok(resolve_jvs_base_url(app))
}

fn decode_wav_b64(input: &str) -> Result<Vec<u8>, String> {
    base64::engine::general_purpose::STANDARD
        .decode(input.trim().as_bytes())
        .map_err(|e| format!("样本 base64 解码失败: {e}"))
}

fn extract_embedding_blocking(base_url: &str, wav: &[u8]) -> Result<Vec<f32>, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())?;
    let part = reqwest::blocking::multipart::Part::bytes(wav.to_vec())
        .mime_str("audio/wav")
        .map_err(|e| e.to_string())?
        .file_name("sample.wav");
    let form = reqwest::blocking::multipart::Form::new().part("audio", part);
    let url = format!("{}/v1/sv/extract", base_url.trim_end_matches('/'));
    let resp = client
        .post(&url)
        .multipart(form)
        .send()
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("JVS SV extract status {}", resp.status()));
    }
    let json: serde_json::Value = resp.json().map_err(|e| e.to_string())?;
    let arr = json
        .get("embedding")
        .and_then(|v| v.as_array())
        .ok_or_else(|| "SV extract 缺少 embedding 字段".to_string())?;
    let mut out = Vec::with_capacity(arr.len());
    for v in arr {
        let Some(f) = v.as_f64() else {
            return Err("SV embedding 含非法数值".to_string());
        };
        out.push(f as f32);
    }
    if out.is_empty() {
        return Err("SV embedding 为空".to_string());
    }
    Ok(out)
}

fn l2_normalize(v: &mut [f32]) {
    let mut sum = 0.0f32;
    for x in v.iter() {
        sum += x * x;
    }
    let denom = sum.sqrt().max(1e-8);
    for x in v.iter_mut() {
        *x /= denom;
    }
}

fn default_voice_profile_path() -> Result<PathBuf, String> {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").map_err(|_| "USERPROFILE not set".to_string())?
    } else {
        std::env::var("HOME").map_err(|_| "HOME not set".to_string())?
    };
    Ok(PathBuf::from(home)
        .join(".jachin")
        .join("voice")
        .join("owner_voiceprint.json"))
}

#[tauri::command]
pub fn enroll_owner_voiceprint(
    app: tauri::AppHandle,
    req: EnrollOwnerVoiceprintRequest,
) -> Result<EnrollOwnerVoiceprintResponse, String> {
    if req.sample_wavs_base64.len() < 3 {
        return Err("至少需要 3 段样本音频".to_string());
    }
    let base_url = ensure_jvs_blocking(&app)?;
    let mut embeds: Vec<Vec<f32>> = Vec::new();
    for s in &req.sample_wavs_base64 {
        let wav = decode_wav_b64(s)?;
        let mut emb = extract_embedding_blocking(&base_url, &wav)?;
        l2_normalize(&mut emb);
        embeds.push(emb);
    }
    let dim = embeds
        .first()
        .map(|v| v.len())
        .ok_or_else(|| "embedding 为空".to_string())?;
    if embeds.iter().any(|v| v.len() != dim) {
        return Err("多段样本 embedding 维度不一致".to_string());
    }
    let mut centroid = vec![0.0f32; dim];
    for emb in &embeds {
        for (i, v) in emb.iter().enumerate() {
            centroid[i] += *v;
        }
    }
    let n = embeds.len() as f32;
    for v in centroid.iter_mut() {
        *v /= n;
    }
    l2_normalize(&mut centroid);

    let now = format!("{:?}", std::time::SystemTime::now());
    let profile = OwnerVoiceprintProfile {
        version: 2,
        model_id: "metis-sv-speech-campplus-zh-cn-16k-common".to_string(),
        sample_count: embeds.len(),
        centroid,
        wake_gate: OwnerVoiceprintWakeGate {
            threshold_high: 0.45,
            threshold_low: 0.31,
        },
        window_label: OwnerVoiceprintWindowLabel {
            win_threshold_high: 0.38,
            win_threshold_low: 0.25,
            win_step_ms: 250,
            win_len_ms: 900,
            min_owner_duration_ms: 300,
            debounce_count: 1,
        },
        created_at: now.clone(),
        updated_at: now,
    };

    let path = default_voice_profile_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("创建 voice 目录失败: {e}"))?;
    }
    let content =
        serde_json::to_string_pretty(&profile).map_err(|e| format!("profile 序列化失败: {e}"))?;
    fs::write(&path, content).map_err(|e| format!("写入 owner_voiceprint.json 失败: {e}"))?;

    Ok(EnrollOwnerVoiceprintResponse {
        ok: true,
        path: path.to_string_lossy().into_owned(),
        sample_count: profile.sample_count,
        embedding_dim: dim,
    })
}

#[derive(Clone, Serialize)]
struct SettingsUpdatedPayload {
    restart_required: bool,
}

#[derive(Clone, Serialize)]
struct DesktopUiLangPayload {
    lang: String,
}

/// 供多 WebView 读取的单一数据源（与 settings.json 一致）
#[tauri::command]
pub fn get_desktop_ui_lang() -> String {
    let s = UserSettings::load();
    match s.desktop_ui_lang.as_deref() {
        Some("en") => "en".to_string(),
        _ => "zh".to_string(),
    }
}

#[tauri::command]
pub fn set_desktop_ui_lang(app: tauri::AppHandle, lang: String) -> Result<(), String> {
    let normalized = if lang == "en" { "en" } else { "zh" };
    let mut s = UserSettings::load();
    s.desktop_ui_lang = Some(normalized.to_string());
    s.save()?;
    app.emit(
        "jachin-desktop-ui-lang-sync",
        DesktopUiLangPayload {
            lang: normalized.to_string(),
        },
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}
