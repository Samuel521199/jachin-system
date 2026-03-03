//! Battle C: Nexus pairing - read/write ~/.jachin/nexus_config.json
//! Same path as Layer 2 daemon for config sharing.
//! HTTP calls done in Rust to avoid CORS.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::process::Command;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const DEFAULT_BASE_URL: &str = "http://localhost:3000";

fn jachin_dir() -> Result<PathBuf, String> {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").map_err(|_| "USERPROFILE not set")?
    } else {
        std::env::var("HOME").map_err(|_| "HOME not set")?
    };
    Ok(PathBuf::from(home).join(".jachin"))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NexusConfig {
    pub instance_id: String,
    pub access_token: String,
    pub nexus_base_url: String,
}

#[derive(Debug, Deserialize)]
pub struct PairingRequestResponse {
    pub session_id: String,
    pub short_code: String,
    pub expires_in: u64,
    pub pair_url: String,
}

#[derive(Debug, Deserialize)]
pub struct PairingStatusResponse {
    pub status: String,
    #[serde(default)]
    pub access_token: Option<String>,
    #[serde(default)]
    pub instance_id: Option<String>,
    #[serde(default)]
    pub nexus_base_url: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
}

fn nexus_config_path() -> Result<PathBuf, String> {
    Ok(jachin_dir()?.join("nexus_config.json"))
}

fn desktop_config_path() -> Result<PathBuf, String> {
    Ok(jachin_dir()?.join("desktop_config.json"))
}

/// Read saved Nexus Base URL (for custom deployment)
#[tauri::command]
pub fn read_nexus_base_url() -> Result<String, String> {
    let path = desktop_config_path()?;
    if !path.exists() {
        return Ok(DEFAULT_BASE_URL.to_string());
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let cfg: serde_json::Value = serde_json::from_str(&content).unwrap_or(serde_json::json!({}));
    Ok(cfg
        .get("nexus_base_url")
        .and_then(|v| v.as_str())
        .unwrap_or(DEFAULT_BASE_URL)
        .to_string())
}

/// Save Nexus Base URL (custom deployment / private Layer 1)
#[tauri::command]
pub fn write_nexus_base_url(url: String) -> Result<(), String> {
    let path = desktop_config_path()?;
    let parent = path.parent().ok_or("Invalid path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let json = serde_json::json!({ "nexus_base_url": url.trim().trim_end_matches('/') });
    fs::write(&path, serde_json::to_string_pretty(&json).unwrap()).map_err(|e| e.to_string())?;
    Ok(())
}

fn find_project_root() -> Result<PathBuf, String> {
    // Try cwd first (user often runs from project root)
    if let Ok(cwd) = std::env::current_dir() {
        if cwd.join("core").join("cli.py").exists() {
            return Ok(cwd);
        }
        if cwd.join("clients").join("desktop").exists() {
            return Ok(cwd);
        }
    }
    // Walk up from executable
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut dir = exe.parent().ok_or("No parent")?.to_path_buf();
    for _ in 0..10 {
        if dir.join("core").join("cli.py").exists() {
            return Ok(dir);
        }
        if dir.join("scripts").join("run-daemon.ps1").exists() {
            return Ok(dir);
        }
        if !dir.pop() {
            break;
        }
    }
    Err("Project root not found (no core/cli.py or scripts/run-daemon.ps1)".to_string())
}

/// CREATE_NO_WINDOW - 彻底静默，不弹出黑色控制台
const CREATE_NO_WINDOW: u32 = 0x08000000;

/// Spawn daemon in background after pairing success (silent, no terminal)
#[tauri::command]
pub fn spawn_daemon() -> Result<bool, String> {
    let root = find_project_root()?;
    let scripts = root.join("scripts");
    #[cfg(target_os = "windows")]
    {
        let ps1 = scripts.join("run-daemon.ps1");
        if !ps1.exists() {
            return Err("scripts/run-daemon.ps1 not found".to_string());
        }
        let mut cmd = Command::new("powershell");
        cmd.args([
            "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden",
            "-NoProfile",
            "-File", ps1.to_str().ok_or("Invalid path")?,
        ])
        .current_dir(&root)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd.spawn().map_err(|e| e.to_string())?;
    }
    #[cfg(not(target_os = "windows"))]
    {
        let sh = scripts.join("run-daemon.sh");
        if !sh.exists() {
            return Err("scripts/run-daemon.sh not found".to_string());
        }
        Command::new("sh")
            .arg(sh.to_str().ok_or("Invalid path")?)
            .current_dir(&root)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(true)
}

/// Check if already paired (config exists and has required fields)
#[tauri::command]
pub fn is_nexus_paired() -> Result<bool, String> {
    let path = nexus_config_path()?;
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let cfg: serde_json::Value = serde_json::from_str(&content).map_err(|e| e.to_string())?;
    let id = cfg.get("instance_id").and_then(|v| v.as_str());
    let token = cfg.get("access_token").and_then(|v| v.as_str());
    Ok(id.map_or(false, |s| !s.is_empty()) && token.map_or(false, |s| !s.is_empty()))
}

/// Write nexus config after successful pairing
#[tauri::command]
pub fn write_nexus_config(config: NexusConfig) -> Result<(), String> {
    let path = nexus_config_path()?;
    let parent = path.parent().ok_or("Invalid config path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let json = serde_json::to_string_pretty(&config).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| e.to_string())?;
    Ok(())
}

/// POST /api/v1/pairing/request - get short_code and session_id
#[tauri::command]
pub async fn pairing_request(base_url: Option<String>) -> Result<PairingRequestResponse, String> {
    let base = base_url.as_deref().unwrap_or(DEFAULT_BASE_URL).trim_end_matches('/');
    let client = reqwest::Client::new();
    let res = client
        .post(format!("{}/api/v1/pairing/request", base))
        .json(&serde_json::json!({
            "environment_type": "desktop",
            "core_version": "1.0.0",
        }))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !res.status().is_success() {
        let status = res.status();
        let body = res.text().await.unwrap_or_default();
        return Err(format!("Pairing request failed ({}): {}", status, body));
    }
    let data: PairingRequestResponse = res.json().await.map_err(|e| e.to_string())?;
    Ok(data)
}

/// GET /api/v1/pairing/status?session_id=xxx - poll until success
#[tauri::command]
pub async fn pairing_status(session_id: String, base_url: Option<String>) -> Result<PairingStatusResponse, String> {
    let base = base_url.as_deref().unwrap_or(DEFAULT_BASE_URL).trim_end_matches('/');
    let client = reqwest::Client::new();
    let res = client
        .get(format!("{}/api/v1/pairing/status?session_id={}", base, session_id))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let data: PairingStatusResponse = res.json().await.map_err(|e| e.to_string())?;
    Ok(data)
}
