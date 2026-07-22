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
    let previous = UserSettings::load();
    let restart_required = settings_change_requires_restart(&previous, &patch);
    patch.save()?;
    app.emit(
        "settings-updated",
        SettingsUpdatedPayload { restart_required },
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

fn settings_change_requires_restart(previous: &UserSettings, next: &UserSettings) -> bool {
    previous.llm_provider_override != next.llm_provider_override
        || previous.stt_provider_override != next.stt_provider_override
        || previous.tts_provider_override != next.tts_provider_override
        || previous.run_mode_override != next.run_mode_override
        || previous.custom_model_path != next.custom_model_path
        || previous.chat_stream_via_direct != next.chat_stream_via_direct
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
pub struct OwnerVoiceprintStatus {
    pub exists: bool,
    pub path: String,
    pub sample_count: Option<usize>,
    pub embedding_dim: Option<usize>,
    pub model_id: Option<String>,
    pub updated_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageContact {
    pub name: String,
    pub kind: String,
    pub aliases: Vec<String>,
    pub shortcut_number: String,
    pub shortcut_letter: String,
    pub enabled: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct MessageContactsBook {
    pub version: u32,
    pub contacts: Vec<MessageContact>,
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

fn jachin_config_dir() -> Result<PathBuf, String> {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").map_err(|_| "USERPROFILE not set".to_string())?
    } else {
        std::env::var("HOME").map_err(|_| "HOME not set".to_string())?
    };
    Ok(PathBuf::from(home).join(".jachin").join("config"))
}

fn message_contacts_path() -> Result<PathBuf, String> {
    Ok(jachin_config_dir()?.join("message_contacts.json"))
}

fn default_message_contacts() -> Vec<MessageContact> {
    vec![
        MessageContact {
            name: "Neil".to_string(),
            kind: "person".to_string(),
            aliases: vec!["Neil".to_string(), "new".to_string(), "n".to_string()],
            shortcut_number: "1".to_string(),
            shortcut_letter: "A".to_string(),
            enabled: true,
        },
        MessageContact {
            name: "Vivian".to_string(),
            kind: "person".to_string(),
            aliases: vec!["Vivian".to_string(), "v".to_string()],
            shortcut_number: "2".to_string(),
            shortcut_letter: "B".to_string(),
            enabled: true,
        },
        MessageContact {
            name: "测试备注冒烟草稿".to_string(),
            kind: "group".to_string(),
            aliases: vec![
                "测试备注冒烟草稿".to_string(),
                "测试备注".to_string(),
                "测试群".to_string(),
                "群聊".to_string(),
                "群".to_string(),
            ],
            shortcut_number: "3".to_string(),
            shortcut_letter: "C".to_string(),
            enabled: true,
        },
    ]
}

#[tauri::command]
pub fn get_message_contacts() -> Result<MessageContactsBook, String> {
    let path = message_contacts_path()?;
    if !path.exists() {
        return Ok(MessageContactsBook {
            version: 1,
            contacts: default_message_contacts(),
        });
    }
    let raw = fs::read_to_string(&path).map_err(|e| format!("读取联系人配置失败: {e}"))?;
    let mut book: MessageContactsBook =
        serde_json::from_str(&raw).map_err(|e| format!("解析联系人配置失败: {e}"))?;
    normalize_message_contacts(&mut book.contacts);
    Ok(book)
}

#[tauri::command]
pub fn save_message_contacts(book: MessageContactsBook) -> Result<MessageContactsBook, String> {
    let mut contacts = book.contacts;
    normalize_message_contacts(&mut contacts);
    let out = MessageContactsBook {
        version: 1,
        contacts,
    };
    let path = message_contacts_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let raw = serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?;
    fs::write(&path, raw).map_err(|e| format!("保存联系人配置失败: {e}"))?;
    Ok(out)
}

fn normalize_message_contacts(contacts: &mut Vec<MessageContact>) {
    let mut out: Vec<MessageContact> = Vec::new();
    for (idx, item) in contacts.iter().cloned().enumerate() {
        let name = item.name.trim().to_string();
        if name.is_empty() {
            continue;
        }
        let mut aliases: Vec<String> = item
            .aliases
            .iter()
            .map(|x| x.trim().to_string())
            .filter(|x| !x.is_empty())
            .collect();
        if !aliases.iter().any(|x| x.eq_ignore_ascii_case(&name)) {
            aliases.insert(0, name.clone());
        }
        out.push(MessageContact {
            name,
            kind: if item.kind.trim().is_empty() {
                "person".to_string()
            } else {
                item.kind.trim().to_string()
            },
            aliases,
            shortcut_number: if item.shortcut_number.trim().is_empty() {
                (idx + 1).to_string()
            } else {
                item.shortcut_number.trim().to_string()
            },
            shortcut_letter: if item.shortcut_letter.trim().is_empty() {
                ((b'A' + (idx as u8).min(25)) as char).to_string()
            } else {
                item.shortcut_letter.trim().to_ascii_uppercase()
            },
            enabled: item.enabled,
        });
    }
    *contacts = out;
}

#[tauri::command]
pub fn get_owner_voiceprint_status() -> Result<OwnerVoiceprintStatus, String> {
    let path = default_voice_profile_path()?;
    if !path.exists() {
        return Ok(OwnerVoiceprintStatus {
            exists: false,
            path: path.to_string_lossy().into_owned(),
            sample_count: None,
            embedding_dim: None,
            model_id: None,
            updated_at: None,
        });
    }
    let raw =
        fs::read_to_string(&path).map_err(|e| format!("读取 owner_voiceprint.json 失败: {e}"))?;
    let json: serde_json::Value =
        serde_json::from_str(&raw).map_err(|e| format!("解析 owner_voiceprint.json 失败: {e}"))?;
    let sample_count = json
        .get("sample_count")
        .and_then(|v| v.as_u64())
        .map(|v| v as usize);
    let embedding_dim = json
        .get("centroid")
        .and_then(|v| v.as_array())
        .map(|v| v.len());
    Ok(OwnerVoiceprintStatus {
        exists: true,
        path: path.to_string_lossy().into_owned(),
        sample_count,
        embedding_dim,
        model_id: json
            .get("model_id")
            .and_then(|v| v.as_str())
            .map(|v| v.to_string()),
        updated_at: json
            .get("updated_at")
            .and_then(|v| v.as_str())
            .map(|v| v.to_string()),
    })
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
            win_threshold_high: 0.44,
            win_threshold_low: 0.25,
            win_step_ms: 250,
            win_len_ms: 900,
            min_owner_duration_ms: 650,
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
